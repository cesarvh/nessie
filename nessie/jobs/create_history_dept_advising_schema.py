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

from flask import current_app as app
from nessie.externals import rds, redshift
from nessie.jobs.background_job import BackgroundJob, BackgroundJobError, verify_external_schema
from nessie.lib.util import resolve_sql_template

"""History Dept Advising schema creation job."""


class CreateHistoryDeptAdvisingSchema(BackgroundJob):

    def run(self):
        app.logger.info('Starting History Dept Advising schema creation job...')
        app.logger.info('Executing SQL...')
        self.create_schema()
        app.logger.info('Redshift schema created. Creating RDS indexes...')
        self.create_indexes()
        return 'History Dept Advising schema creation job completed.'

    def create_schema(self):
        external_schema = app.config['REDSHIFT_SCHEMA_HISTORY_DEPT_ADVISING']
        redshift.drop_external_schema(external_schema)

        resolved_ddl = resolve_sql_template('create_history_dept_advising_schema.template.sql')
        if redshift.execute_ddl_script(resolved_ddl):
            verify_external_schema(external_schema, resolved_ddl)
        else:
            raise BackgroundJobError('History Dept Advising schema creation job failed.')

    def create_indexes(self):
        resolved_ddl = resolve_sql_template('index_history_dept_advising.template.sql')
        if rds.execute(resolved_ddl):
            app.logger.info('Created History Dept Advising RDS indexes.')
        else:
            raise BackgroundJobError('History Dept Advising schema creation job failed to create indexes.')
