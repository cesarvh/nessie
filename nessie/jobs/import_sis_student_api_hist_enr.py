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
from concurrent.futures import ThreadPoolExecutor
from itertools import repeat
import json
import tempfile
from timeit import default_timer as timer

from flask import current_app as app
from nessie.externals import redshift
from nessie.externals.sis_student_api import get_v2_by_sids_list
from nessie.jobs.background_job import BackgroundJob, BackgroundJobError
from nessie.lib.queries import get_unfetched_non_advisees, student_schema, student_schema_table
from nessie.lib.util import encoded_tsv_row, resolve_sql_template_string
from nessie.models.student_schema_manager import truncate_staging_table, write_file_to_staging

"""Logic for SIS student API import job for non-advisees."""


def async_get_feeds(app_obj, up_to_100_sids):
    with app_obj.app_context():
        feeds = get_v2_by_sids_list(up_to_100_sids, with_contacts=False)
        result = {
            'sids': up_to_100_sids,
            'feeds': feeds,
        }
    return result


class ImportSisStudentApiHistEnr(BackgroundJob):

    max_threads = app.config['STUDENT_API_MAX_THREADS']

    def run(self, sids=None):
        if not sids:
            sids = [row['sid'] for row in get_unfetched_non_advisees()]
        app.logger.info(f'Starting SIS student API import job for {len(sids)} non-advisees...')

        with tempfile.TemporaryFile() as feed_file:
            saved_sids, failure_count = self.load_concurrently(sids, feed_file)
            if saved_sids:
                sis_profiles_hist_enr = student_schema_table('sis_profiles_hist_enr')
                truncate_staging_table(sis_profiles_hist_enr)
                write_file_to_staging(sis_profiles_hist_enr, feed_file, len(saved_sids))

        if saved_sids:
            staging_to_destination_query = resolve_sql_template_string(
                """
                DELETE FROM {redshift_schema}.{sis_profiles_hist_enr} WHERE sid IN
                    (SELECT sid FROM {redshift_schema}_staging.{sis_profiles_hist_enr});
                INSERT INTO {redshift_schema}.{sis_profiles_hist_enr}
                    (SELECT * FROM {redshift_schema}_staging.{sis_profiles_hist_enr});
                TRUNCATE {redshift_schema}_staging.{sis_profiles_hist_enr};
                """,
                redshift_schema=student_schema(),
                sis_profiles_hist_enr=sis_profiles_hist_enr,
            )
            if not redshift.execute(staging_to_destination_query):
                raise BackgroundJobError('Error on Redshift copy: aborting job.')

        return f'SIS student API non-advisee import job completed: {len(saved_sids)} succeeded, {failure_count} failed.'

    def load_concurrently(self, all_sids, feed_file):
        chunked_sids = [all_sids[i:i + 100] for i in range(0, len(all_sids), 100)]
        saved_sids = []
        failure_count = 0
        app_obj = app._get_current_object()
        start_loop = timer()

        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            for result in executor.map(async_get_feeds, repeat(app_obj), chunked_sids):
                remaining_sids = set(result['sids'])
                feeds = result['feeds']
                if feeds:
                    for feed in feeds:
                        sid = next((_id.get('id') for _id in feed['identifiers'] if _id.get('type') == 'student-id'), None)
                        uid = next((_id.get('id') for _id in feed['identifiers'] if _id.get('type') == 'campus-uid'), None)
                        if not sid or not uid:
                            continue
                        feed_file.write(encoded_tsv_row([sid, uid, json.dumps(feed)]) + b'\n')
                        remaining_sids.discard(sid)
                        saved_sids.append(sid)
                if remaining_sids:
                    failure_count = failure_count + len(remaining_sids)
                    app.logger.error(f'SIS student API import failed for non-advisees {remaining_sids}.')

        app.logger.info(f'Wanted {len(all_sids)} non-advisees; got {len(saved_sids)} in {timer() - start_loop} secs')
        return saved_sids, failure_count
