"""
Copyright ©2022. The Regents of the University of California (Regents). All Rights Reserved.

Permission to use, copy, modify, and distribute this software and its documentation
for educational, research, and not-for-profit purposes, without fee and without a
signed licensing agreement, is hereby granted, provided that the above copyright
notice, this paragraph and the following two paragraphs appear in all copies,
modifications, and distributions.

Contact The Office of Technology Licensing, UC Berkeley, 2150 Shattuck Avenue,
Suite 510, Berkeley, CA 94720-1620, (510) 643-7201, otl@berkeley.edu,
http://ipira.berkeley.edu/industry-info for commercial licensing opportunities.

IN NO EVENT SHALL REGENTS BE LIABLE TO ANY PARTY FOR DIRECT, INDIRECT, SPECIAL,
INCIDENTAL, OR CONSEQUENTIAL DAMAGES, INCLUDING LOST PROFITS, ARISING OUT OF
THE USE OF THIS SOFTWARE AND ITS DOCUMENTATION, EVEN IF REGENTS HAS BEEN ADVISED
OF THE POSSIBILITY OF SUCH DAMAGE.

REGENTS SPECIFICALLY DISCLAIMS ANY WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. THE
SOFTWARE AND ACCOMPANYING DOCUMENTATION, IF ANY, PROVIDED HEREUNDER IS PROVIDED
"AS IS". REGENTS HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES,
ENHANCEMENTS, OR MODIFICATIONS.
"""

import json
from operator import itemgetter
import re

from flask import current_app as app
from nessie.lib.berkeley import career_code_to_name, degree_program_url_for_major, term_name_for_sis_id
from nessie.lib.util import to_float, vacuum_whitespace


def parse_merged_sis_profile(feed_elements):
    sis_profile_feed = feed_elements.get('sis_profile_feed')
    degree_progress_feed = feed_elements.get('degree_progress_feed')
    last_registration_feed = feed_elements.get('last_registration_feed')
    intended_majors_feed = feed_elements.get('intended_majors')

    sis_profile_feed = sis_profile_feed and json.loads(sis_profile_feed, strict=False)
    if not sis_profile_feed:
        return False

    sis_profile = {}

    # We sometimes get malformed feed structures, most often in the form of
    # duplicate wrapped dictionaries (BOAC-362, NS-202, NS-203). Retrieve as much as we
    # can, separately handling exceptions in different parts of the feed.
    for merge_method in [
        merge_academic_standing,
        merge_sis_profile_academic_status,
        merge_sis_profile_emails,
        merge_sis_profile_names,
        merge_sis_profile_phones,
        merge_holds,
        merge_term_gpa,
    ]:
        try:
            merge_method(sis_profile_feed, sis_profile)
        except AttributeError as e:
            app.logger.error(f'Malformed data in sis_profile_feed: {sis_profile_feed}')
            app.logger.error(e)

    merge_registration(sis_profile_feed, last_registration_feed, sis_profile)
    if sis_profile.get('academicCareer') == 'UGRD':
        sis_profile['degreeProgress'] = degree_progress_feed and json.loads(degree_progress_feed)
    sis_profile['intendedMajors'] = merge_intended_majors(intended_majors_feed)
    return sis_profile


def merge_academic_standing(sis_profile_feed, sis_profile):
    sis_profile['academicStanding'] = sis_profile_feed.get('academicStanding', {})


def merge_holds(sis_profile_feed, sis_profile):
    sis_profile['holds'] = sis_profile_feed.get('holds', [])


def merge_sis_profile_academic_status(sis_profile_feed, sis_profile):
    # It is possible to receive multiple academic statuses. We'll prefer an undergraduate enrollment, but
    # otherwise select the first well-formed status that is not a Law enrollment.
    academic_status = None
    for status in sis_profile_feed.get('academicStatuses', []):
        status_code = status.get('studentCareer', {}).get('academicCareer', {}).get('code')
        if status_code and status_code == 'UGRD':
            academic_status = status
            break
        elif status_code in {'UCBX', 'GRAD'}:
            academic_status = status
            next
    if not academic_status:
        return
    career_code = academic_status['studentCareer']['academicCareer']['code']
    sis_profile['academicCareer'] = career_code
    sis_profile['academicCareerStatus'] = parse_career_status(career_code, sis_profile_feed)
    sis_profile['calnetAffiliations'] = sis_profile_feed.get('calnet', {}).get('affiliations', [])

    if sis_profile.get('academicCareerStatus') == 'Completed':
        merge_degrees(sis_profile_feed, sis_profile, academic_status)

    cumulative_units = None
    cumulative_units_taken_for_gpa = None

    for units in academic_status.get('cumulativeUnits', []):
        code = units.get('type', {}).get('code')
        if code == 'Total':
            cumulative_units = units.get('unitsCumulative')
        elif code == 'For GPA':
            cumulative_units_taken_for_gpa = units.get('unitsTaken')

    sis_profile['cumulativeUnits'] = cumulative_units

    cumulative_gpa = academic_status.get('cumulativeGPA', {}).get('average')
    if cumulative_gpa == 0 and not cumulative_units_taken_for_gpa:
        sis_profile['cumulativeGPA'] = None
    else:
        sis_profile['cumulativeGPA'] = cumulative_gpa

    sis_profile['termsInAttendance'] = academic_status.get('termsInAttendance')

    merge_sis_profile_matriculation(academic_status, sis_profile)
    merge_sis_profile_plans(academic_status, sis_profile)


def parse_career_status(career_code, sis_profile_feed):
    # Try to derive a coherent career-status from the SIS affiliations.
    career_status = None
    for affiliation in sis_profile_feed.get('affiliations', []):
        if affiliation.get('type', {}).get('code') == career_code_to_name(career_code):
            if affiliation.get('detail') == 'Completed':
                career_status = 'Completed'
            else:
                career_status = affiliation.get('status', {}).get('description')
                if career_status == 'Error':
                    career_status = affiliation.get('status', {}).get('formalDescription')
            break
    if not career_status:
        app.logger.warning(f'Conflict between affiliations and academicStatuses in profile feed: {sis_profile_feed}')
    return career_status


def merge_degrees(sis_profile_feed, sis_profile, academic_status):
    degrees = []
    # Look up 'studentCareer' completion date, which we believe is graduation date.
    completion_date = academic_status.get('studentCareer', {}).get('toDate')
    if completion_date:
        sis_profile['academicCareerCompleted'] = completion_date

    def is_awarded(d):
        return d.get('status', {}).get('description') == 'Awarded'
    degrees_awarded = [d for d in sis_profile_feed.get('degrees', []) if is_awarded(d)]

    for degree in degrees_awarded:
        description = degree.get('academicDegree', {}).get('type', {}).get('description')
        plans = []
        for plan in degree.get('academicPlans', []):
            plan_type = plan.get('type', {}).get('code')
            target_degree = plan.get('targetDegree', {}).get('type', {}).get('description', {})
            if target_degree == description or plan_type == 'MIN':
                # formalDescription seems helpful for MIN plans but not otherwise.
                if plan_type == 'MIN':
                    plan_desc = plan.get('plan', {}).get('formalDescription')
                else:
                    plan_desc = plan.get('plan', {}).get('description')
                plans.append({
                    'group': plan.get('academicProgram', {}).get('academicGroup', {}).get('formalDescription'),
                    'plan': plan_desc,
                    'type': plan_type,
                })
        degrees.append({
            'dateAwarded': degree.get('dateAwarded'),
            'description': description,
            'plans': plans,
        })
    sis_profile['degrees'] = degrees


def merge_registration(sis_profile_feed, last_registration_feed, sis_profile):
    registration = next((r for r in sis_profile_feed.get('registrations', [])), None)
    # If student is not officially registered in the current term, the feed may not include a 'registrations' element.
    # In that case, we find most recent registration-hosted data in the fuller 'last_registration' feed.
    if not registration:
        registration = last_registration_feed and json.loads(last_registration_feed)
    if not registration:
        return

    if not sis_profile.get('academicCareer'):
        sis_profile['academicCareer'] = registration.get('academicCareer', {}).get('code')

    term_units = registration.get('termUnits', [])
    # SIS has been inconsistent w.r.t. termUnits.type.description value. We tolerate the variance.
    total_units = next((u for u in term_units if u['type']['description'] in ['Total', 'Total Units']), {})

    # The old 'academicLevel' element has become at least two 'academicLevels': one for the beginning-of-term, one
    # for the end-of-term. The beginning-of-term level should match what V1 gave us.
    levels = registration.get('academicLevels', [])
    if levels:
        # If the latest-term is in the past, then it probably makes sense to show the student's academic level
        # as it was expected to be at the End-of-Term.
        is_pending = (not total_units.get('unitsTaken')) and total_units.get('unitsEnrolled')
        level_type = 'BOT' if is_pending else 'EOT'
        for level in levels:
            # For Summer Session visitors, SIS may return a data-free 'academicLevels' element.
            if level.get('level') and level.get('type', {}).get('code') == level_type:
                sis_profile['level'] = level['level']
                break

    if total_units:
        sis_profile['currentTerm'] = {}
        units_max = total_units.get('unitsMax')
        sis_profile['currentTerm']['unitsMax'] = to_float(units_max) if units_max is not None else None
        units_min = total_units.get('unitsMin')
        sis_profile['currentTerm']['unitsMin'] = to_float(units_min) if units_min is not None else None

    # TODO Should we also check for ['academicStanding']['status'] == {'code': 'DIS', 'description': 'Dismissed'}?
    withdrawal_cancel = registration.get('withdrawalCancel', {})
    if withdrawal_cancel:
        term_id = registration.get('term', {}).get('id')
        sis_profile['withdrawalCancel'] = {
            'description': withdrawal_cancel.get('type', {}).get('description'),
            'reason': withdrawal_cancel.get('reason', {}).get('description'),
            'date': withdrawal_cancel.get('date'),
            'termId': term_id,
        }


def merge_sis_profile_emails(sis_profile_feed, sis_profile):
    primary_email = None
    campus_email = None
    for email in sis_profile_feed.get('emails', []):
        if email.get('primary'):
            primary_email = email.get('emailAddress')
        elif email.get('type', {}).get('code') == 'CAMP':
            campus_email = email.get('emailAddress')
    sis_profile['emailAddress'] = campus_email or primary_email
    if primary_email and campus_email and primary_email != campus_email:
        sis_profile['emailAddressAlternate'] = primary_email


def merge_sis_profile_matriculation(academic_status, sis_profile):
    matriculation = academic_status.get('studentCareer', {}).get('matriculation')
    if matriculation:
        matriculation_term_name = matriculation.get('term', {}).get('name')
        if matriculation_term_name and re.match('\A2\d{3} (?:Spring|Summer|Fall)\Z', matriculation_term_name):
            # "2015 Fall" to "Fall 2015"
            sis_profile['matriculation'] = ' '.join(reversed(matriculation_term_name.split()))
        if matriculation.get('type', {}).get('code') == 'TRN':
            sis_profile['transfer'] = True
    if not sis_profile.get('transfer'):
        sis_profile['transfer'] = False


def merge_sis_profile_names(sis_profile_feed, sis_profile):
    for name in sis_profile_feed.get('names', []):
        code = name.get('type', {}).get('code')
        if code == 'PRF':
            sis_profile['preferredName'] = vacuum_whitespace(name.get('formattedName'))
        elif code == 'PRI':
            sis_profile['primaryName'] = vacuum_whitespace(name.get('formattedName'))
        if 'primaryName' in sis_profile and 'preferredName' in sis_profile:
            break


def merge_sis_profile_phones(sis_profile_feed, sis_profile):
    phones_by_code = {
        phone.get('type', {}).get('code'): phone.get('number')
        for phone in sis_profile_feed.get('phones', [])
    }
    sis_profile['phoneNumber'] = phones_by_code.get('CELL') or phones_by_code.get('LOCL') or phones_by_code.get('HOME')


def merge_sis_profile_plans(academic_status, sis_profile):
    plans = []
    plans_minor = []
    subplans = set()
    for student_plan in academic_status.get('studentPlans', []):
        academic_plan = student_plan.get('academicPlan', {})
        # SIS majors come in five flavors, plus a sixth for minors.
        if academic_plan.get('type', {}).get('code') not in ['MAJ', 'SS', 'SP', 'HS', 'CRT', 'MIN']:
            continue
        plan = academic_plan.get('plan', {})
        description = plan.get('description')
        plan_feed = {
            'degreeProgramUrl': degree_program_url_for_major(description),
            'description': description,
        }
        # Find the latest expected graduation term from any plan.
        expected_graduation_term = student_plan.get('expectedGraduationTerm', {}).get('id')
        if expected_graduation_term and expected_graduation_term > sis_profile.get('expectedGraduationTerm', {}).get('id', '0'):
            sis_profile['expectedGraduationTerm'] = {
                'id': expected_graduation_term,
                'name': term_name_for_sis_id(expected_graduation_term),
            }

        program = student_plan.get('academicPlan', {}).get('academicProgram', {}).get('program', {})
        plan_feed['program'] = program.get('formalDescription') or program.get('description')

        # Add plan status.
        plan_status = student_plan.get('statusInPlan', {}).get('status')
        if plan_status:
            plan_feed['status'] = plan_status.get('formalDescription') or plan_status.get('description')
            # We generally prefer the 'formalDescription', but our formality has limits.
            if plan_feed['status'] == 'Active in Program':
                plan_feed['status'] = 'Active'
        else:
            # A plan with no status is considered discontinued. (NS-689)
            plan_feed['status'] = 'Discontinued'

        # Add plan unless it's a duplicate.
        if academic_plan.get('type', {}).get('code') == 'MIN':
            plan_collection = plans_minor
        else:
            plan_collection = plans
        if not next((p for p in plan_collection if p.get('description') == plan_feed.get('description')), None):
            plan_collection.append(plan_feed)

        # Add any subplans.
        for academic_subplan in student_plan.get('academicSubPlans', []):
            subplan_description = academic_subplan.get('subPlan', {}).get('description')
            if subplan_description:
                subplans.add(subplan_description)

    sis_profile['plans'] = sorted(plans, key=itemgetter('description'))
    sis_profile['plansMinor'] = sorted(plans_minor, key=itemgetter('description'))
    sis_profile['subplans'] = sorted(list(subplans))


def merge_intended_majors(intended_majors_feed):
    intended_majors = None
    if intended_majors_feed:
        unique_codes = []
        intended_majors = []
        for intended_major in intended_majors_feed.split(' || '):
            code = intended_major.split(' :: ')[0]
            if code not in unique_codes:
                unique_codes.append(code)
                description = intended_major.split(' :: ')[1]
                intended_majors.append({
                    'code': code,
                    'description': description,
                    'degreeProgramUrl': degree_program_url_for_major(description),
                })
        intended_majors = sorted(intended_majors, key=itemgetter('description'))
    return intended_majors


def merge_term_gpa(sis_profile_feed, sis_profile):
    sis_profile['termGpa'] = sis_profile_feed.get('termGpa', [])
