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

import re

from nessie.externals import sis_degree_progress_api
from nessie.lib.mockingbird import MockResponse, register_mock


class TestSisDegreeProgressApi:
    """SIS Degree Progress API query."""

    def test_parsed(self, app):
        """Returns the front-end-friendly data."""
        parsed = sis_degree_progress_api.parsed_degree_progress(11667051)
        assert parsed['reportDate'] == '2017-03-03'
        reqs = parsed['requirements']
        assert reqs['entryLevelWriting']['status'] == 'Satisfied'
        assert reqs['americanHistory']['status'] == 'Not Satisfied'
        assert reqs['americanCultures']['status'] == 'In Progress'
        assert reqs['americanInstitutions']['status'] == 'Not Satisfied'

    def test_get_degree_progress(self, app):
        """Returns unwrapped data."""
        xml_dict = sis_degree_progress_api.get_degree_progress(11667051)
        degree_progress = xml_dict['UC_AA_PROGRESS']['PROGRESSES']['PROGRESS']
        assert degree_progress['RPT_DATE'] == '2017-03-03'
        assert degree_progress['REQUIREMENTS']['REQUIREMENT'][1]['NAME'] == 'American History (R-0002)'

    def test_inner_get_degree_progress(self, app):
        """Returns fixture data."""
        oski_response = sis_degree_progress_api._get_degree_progress(11667051)
        assert oski_response
        assert oski_response.status_code == 200
        xml = oski_response.text
        assert re.search(r'<UC_AA_PROGRESS>', xml)

    def test_user_not_found(self, app, caplog):
        """Returns empty when CS delivers an error in the XML."""
        response = sis_degree_progress_api._get_degree_progress(9999999)
        assert response
        parsed = sis_degree_progress_api.parsed_degree_progress(9999999)
        assert parsed == {}

    def test_server_error(self, app, caplog):
        """Logs unexpected server errors and returns informative message."""
        api_error = MockResponse(500, {}, '{"message": "Internal server error."}')
        with register_mock(sis_degree_progress_api._get_degree_progress, api_error):
            response = sis_degree_progress_api._get_degree_progress(11667051)
            assert '500 Server Error' in caplog.text
            assert not response
            assert response.raw_response.status_code == 500
            assert response.raw_response.json()['message']
