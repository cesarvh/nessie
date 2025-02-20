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

from datetime import datetime, timedelta

from flask import current_app as app
from nessie.externals import rds, redshift
from nessie.jobs.background_job import BackgroundJob, BackgroundJobError
from nessie.lib.berkeley import next_term_id, term_name_for_sis_id
from nessie.lib.queries import edl_external_schema
import pytz

"""Logic for SIS Terms schema creation job."""


class CreateTermsSchema(BackgroundJob):

    rds_schema = app.config['RDS_SCHEMA_TERMS']
    redshift_schema = app.config['REDSHIFT_SCHEMA_TERMS']

    def run(self):
        app.logger.info('Starting SIS terms schema creation job...')
        self.refresh_sis_term_definitions()
        self.refresh_current_term_index()
        return 'SIS terms schema creation job completed.'

    def refresh_sis_term_definitions(self):
        rows = redshift.fetch(f"""
            SELECT
              semester_year_term_cd AS term_id,
              semester_year_name_concat_2 as term_name,
              TO_CHAR(semester_first_day_of_insr_dt, 'YYYY-MM-DD') AS term_begins,
              TO_CHAR(term_end_dt, 'YYYY-MM-DD') AS term_ends
            FROM {edl_external_schema()}.student_academic_terms_data
            WHERE
              semester_year_term_cd >= {app.config['EARLIEST_ACADEMIC_HISTORY_TERM_ID']}
              AND academic_career_cd = 'UGRD'
            ORDER BY semester_year_term_cd
        """)

        if len(rows):
            with rds.transaction() as transaction:
                if self.refresh_rds(rows, transaction):
                    transaction.commit()
                    app.logger.info('Refreshed RDS indexes.')
                else:
                    transaction.rollback()
                    raise BackgroundJobError('Error refreshing RDS term definitions.')

    def refresh_rds(self, rows, transaction):
        result = transaction.execute(f'TRUNCATE {self.rds_schema}.term_definitions')
        if not result:
            return False
        columns = ['term_id', 'term_name', 'term_begins', 'term_ends']
        result = transaction.insert_bulk(
            f'INSERT INTO {self.rds_schema}.term_definitions ({", ".join(columns)}) VALUES %s',
            [tuple([r[c] for c in columns]) for r in rows],
        )
        if not result:
            return False
        return True

    def refresh_current_term_index(self):
        today = datetime.now(pytz.utc).astimezone(pytz.timezone(app.config['TIMEZONE'])).date()
        current_term = self.get_sis_current_term(today)

        if current_term:
            term_id = current_term['term_id']

            # Check if the advance enrollment period has started for the next two upcoming terms.
            future_term_id = term_id
            for _ in range(2):
                term_id = next_term_id(term_id)
                term = self.get_sis_term_for_id(term_id)
                advance_enrollment_period = 0
                if term_id[3] == '2':
                    advance_enrollment_period = 95
                elif term_id[3] == '5':
                    advance_enrollment_period = 124
                elif term_id[3] == '8':
                    advance_enrollment_period = 140
                if term['term_begins'] - timedelta(days=advance_enrollment_period) < today:
                    future_term_id = term_id

            with rds.transaction() as transaction:
                transaction.execute(f'TRUNCATE {self.rds_schema}.current_term_index')
                columns = ['current_term_name', 'future_term_name']
                values = tuple([current_term['term_name'], term_name_for_sis_id(future_term_id)])
                if transaction.execute(f'INSERT INTO {self.rds_schema}.current_term_index ({", ".join(columns)}) VALUES {values} '):
                    transaction.commit()
                else:
                    transaction.rollback()
                    raise BackgroundJobError('Error refreshing RDS current term index.')

    def get_sis_current_term(self, for_date):
        rows = rds.fetch(
            f"""SELECT *, DATE(term_ends + INTERVAL '10 DAYS') AS grace_period_ends
                FROM {self.rds_schema}.term_definitions
                WHERE DATE(term_ends + INTERVAL '10 DAYS') >= '{for_date}'
                ORDER BY term_id ASC LIMIT 2""",
        )
        if rows:
            return rows[1] if (for_date >= rows[1]['term_begins'] or for_date > rows[0]['grace_period_ends']) else rows[0]

    def get_sis_term_for_id(self, term_id):
        sql = f"SELECT * FROM {self.rds_schema}.term_definitions WHERE term_id = '{term_id}' LIMIT 1"
        rows = rds.fetch(sql)
        return rows and rows[0]
