"""
Copyright ©2021. The Regents of the University of California (Regents). All Rights Reserved.

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

from flask import current_app as app
from nessie.lib import http
from nessie.lib.mockingbird import fixture
from requests.auth import HTTPBasicAuth
import xmltodict

"""Official access to undergraduate degree progress."""


def parsed_degree_progress(cs_id):
    cs_feed = get_degree_progress(cs_id)
    if cs_feed is None:
        return None
    data = {}
    requirements_list = (
        cs_feed and cs_feed.get('UC_AA_PROGRESS', {}).get('PROGRESSES', {}).get('PROGRESS', {}).get('REQUIREMENTS', {}).get('REQUIREMENT')
    )
    if requirements_list:
        data['reportDate'] = cs_feed['UC_AA_PROGRESS']['PROGRESSES']['PROGRESS']['RPT_DATE']
        data['requirements'] = {
            'entryLevelWriting': {'name': 'Entry Level Writing'},
            'americanHistory': {'name': 'American History'},
            'americanInstitutions': {'name': 'American Institutions'},
            'americanCultures': {'name': 'American Cultures'},
        }
        for req in requirements_list:
            merge_requirement_status(data['requirements'], req)
    return data


def merge_requirement_status(data, req):
    code = req.get('CODE')
    if code:
        code = int(req.get('CODE'))
        if code == 1:
            key = 'entryLevelWriting'
        elif code == 2:
            key = 'americanHistory'
        elif code == 3:
            key = 'americanCultures'
        elif code == 18:
            key = 'americanInstitutions'
        else:
            return
        if req.get('IN_PROGRESS') == 'Y':
            status = 'In Progress'
        elif req.get('STATUS') == 'COMP':
            status = 'Satisfied'
        else:
            status = 'Not Satisfied'
        data[key]['status'] = status


def get_degree_progress(cs_id):
    response = _get_degree_progress(cs_id)
    if response:
        de_xmled = xmltodict.parse(response.text)
        if de_xmled.get('UC_AA_PROGRESS'):
            return de_xmled
        else:
            return False
    else:
        if hasattr(response, 'raw_response') and hasattr(response.raw_response, 'status_code') and response.raw_response.status_code == 404:
            return False
        else:
            return None


@fixture('sis_degree_progress_{cs_id}.xml')
def _get_degree_progress(cs_id, mock=None):
    url = http.build_url(app.config['DEGREE_PROGRESS_API_URL'], {'EMPLID': cs_id})
    with mock(url):
        return http.request(url, auth=cs_api_auth(), log_404s=False)


def cs_api_auth():
    return HTTPBasicAuth(app.config['DEGREE_PROGRESS_API_USERNAME'], app.config['DEGREE_PROGRESS_API_PASSWORD'])
