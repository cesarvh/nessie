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
from nessie.externals import rds, redshift
from nessie.jobs.background_job import BackgroundJob, BackgroundJobError
from nessie.lib.berkeley import reverse_term_ids, term_name_for_sis_id
from nessie.lib.util import hashed_datestamp, resolve_sql_template

"""Logic for BOAC analytics job."""


class GenerateBoacAnalytics(BackgroundJob):
    s3_boa_path = f"s3://{app.config['LOCH_S3_BUCKET']}/" + app.config['LOCH_S3_BOAC_ANALYTICS_DATA_PATH']

    def run(self):
        app.logger.info('Starting BOAC analytics job...')

        term_id_series = reverse_term_ids()
        boac_snapshot_daily_path = f'{self.s3_boa_path}/term/{term_id_series[0]}/daily/{hashed_datestamp()}'
        resolved_ddl = resolve_sql_template(
            'create_boac_schema.template.sql',
            boac_snapshot_daily_path=boac_snapshot_daily_path,
            current_term_id=term_id_series[0],
            last_term_id=term_id_series[1],
            previous_term_id=term_id_series[2],
        )
        if not redshift.execute_ddl_script(resolved_ddl):
            raise BackgroundJobError('BOAC analytics creation job failed.')

        boac_assignments_path = f'{self.s3_boa_path}/assignment_submissions_relative'
        for term_id in term_id_series:
            term_name = term_name_for_sis_id(term_id)
            resolved_ddl = resolve_sql_template(
                'unload_assignment_submissions.template.sql',
                boac_assignments_path=boac_assignments_path,
                term_id=term_id,
                term_name=term_name,
            )
            if not redshift.execute_ddl_script(resolved_ddl):
                raise BackgroundJobError(f'Assignment submissions upload failed for term {term_id}.')

        resolved_ddl_rds = resolve_sql_template('update_rds_indexes_boac.template.sql')
        if not rds.execute(resolved_ddl_rds):
            raise BackgroundJobError('Failed to update RDS indexes for BOAC analytics schema.')

        return 'BOAC analytics creation job completed.'
