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

from botocore.exceptions import ClientError, ConnectionError
from flask import current_app as app
from nessie.externals import canvas_api, s3
from nessie.jobs.background_job import BackgroundJob
from nessie.lib.metadata import update_canvas_api_import_status
from nessie.lib.mockingbird import fixture

"""Canvas API import."""


class ImportCanvasApiData(BackgroundJob):

    def run(self, course_id, path, s3_key, job_id, mock=None):
        update_canvas_api_import_status(job_id, course_id, 'started')
        try:
            feed = self._fetch_canvas_api_data(path, mock=mock)
            if not feed:
                update_canvas_api_import_status(job_id, course_id, 'no_data')
                return True
            for page_number, page in enumerate(feed):
                if self._first_page_empty(page_number, page):
                    update_canvas_api_import_status(job_id, course_id, 'no_data')
                    return True
                with s3.stream_upload(page, f'{s3_key}_{page_number}.json'):
                    update_canvas_api_import_status(job_id, course_id, status='streaming', details=f'page_number={page_number}')
        except (ClientError, ConnectionError, ValueError) as e:
            update_canvas_api_import_status(job_id, course_id, 'error', details=str(e))
            app.logger.error(e)
            return False
        update_canvas_api_import_status(job_id, course_id, 'complete')
        return True

    def _first_page_empty(self, page_number, page):
        # Some Canvas APIs (e.g. Grade Change Log) return a small but non-empty response when there is no data.
        return page_number == 0 and len(page.text) < 500

    @fixture('canvas_course_grade_change_log_7654321.json')
    def _fetch_canvas_api_data(self, path, mock=None):
        return canvas_api.paged_request(path=path, mock=mock)
