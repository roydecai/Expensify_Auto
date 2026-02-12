import sqlite3
import os
from contextlib import contextmanager

DB_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'db')

class DBManager:
    def __init__(self, db_folder=DB_FOLDER):
        self.db_folder = db_folder
        if not os.path.exists(self.db_folder):
            os.makedirs(self.db_folder)

    def _get_db_path(self, db_name):
        return os.path.join(self.db_folder, db_name)

    def _get_columns(self, conn, table_name):
        cursor = conn.execute(f'PRAGMA table_info("{table_name}")')
        return {row[1] for row in cursor.fetchall()}

    def _add_missing_columns(self, conn, table_name, columns):
        existing = self._get_columns(conn, table_name)
        for column_name, column_type in columns:
            if column_name not in existing:
                conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN {column_name} {column_type}')

    def _rename_column_if_needed(self, conn, table_name, old_name, new_name):
        existing = self._get_columns(conn, table_name)
        if old_name in existing and new_name not in existing:
            conn.execute(f'ALTER TABLE "{table_name}" RENAME COLUMN {old_name} TO {new_name}')

    @contextmanager
    def get_connection(self, db_name):
        db_path = self._get_db_path(db_name)
        conn = sqlite3.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def init_company_db(self):
        with self.get_connection('company.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS companies (
                    payer_tax_id TEXT PRIMARY KEY,
                    full_name TEXT,
                    short_name TEXT,
                    eng_full_name TEXT,
                    eng_short_name TEXT,
                    main_bank TEXT,
                    main_bank_account TEXT,
                    tax_authority TEXT,
                    expensify_policyID TEXT,
                    update_date TEXT
                )
            ''')
            self._add_missing_columns(conn, 'companies', [
                ('expensify_policyID', 'TEXT'),
                ('update_date', 'TEXT')
            ])
            conn.commit()
            print("company.db initialized.")

    def init_suppliers_db(self):
        with self.get_connection('suppliers.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS suppliers (
                    supplier_tax_id TEXT PRIMARY KEY,
                    full_name TEXT,
                    short_name TEXT,
                    main_bank TEXT,
                    main_bank_account TEXT,
                    other_bank_infos TEXT,
                    internal_contactor TEXT,
                    update_date TEXT
                )
            ''')
            self._add_missing_columns(conn, 'suppliers', [
                ('update_date', 'TEXT')
            ])
            conn.commit()
            print("suppliers.db initialized.")

    def init_transaction_dbs(self):
        for db_name in ['temp_VAT_invoice.db', 'temp_bank_outbound.db', 'temp_tax_payment.db']:
            with self.get_connection(db_name) as conn:
                cursor = conn.cursor()
                cursor.execute('CREATE TABLE IF NOT EXISTS _metadata (key TEXT PRIMARY KEY, value TEXT)')
                conn.commit()
                print(f"{db_name} initialized.")

    def init_employee_db(self):
        with self.get_connection('employee.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS employees (
                    employee_id TEXT PRIMARY KEY,
                    full_name TEXT,
                    email_address TEXT,
                    department TEXT
                )
            ''')
            conn.commit()
            print("employee.db initialized.")

    def create_payer_tables(self, payer_tax_id):
        safe_id = payer_tax_id.replace('-', '_').replace(' ', '')

        with self.get_connection('temp_VAT_invoice.db') as conn:
            cursor = conn.cursor()
            table_name = f"invoice_{safe_id}"
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS "{table_name}" (
                    invoice_number TEXT PRIMARY KEY,
                    date TEXT,
                    total_amount REAL,
                    tax_amount REAL,
                    currency TEXT,
                    items TEXT,
                    seller_tax_id TEXT,
                    upload_date TEXT
                )
            ''')
            self._rename_column_if_needed(conn, table_name, 'amount', 'total_amount')
            self._add_missing_columns(conn, table_name, [
                ('total_amount', 'REAL'),
                ('tax_amount', 'REAL'),
                ('upload_date', 'TEXT')
            ])
            conn.commit()
            print(f"Table {table_name} created in temp_VAT_invoice.db")

        with self.get_connection('temp_bank_outbound.db') as conn:
            cursor = conn.cursor()
            table_name = f"bank_slip_{safe_id}"
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS "{table_name}" (
                    bank_slip_id TEXT PRIMARY KEY,
                    date TEXT,
                    amount REAL,
                    currency TEXT,
                    receiver_id TEXT,
                    upload_date TEXT
                )
            ''')
            self._add_missing_columns(conn, table_name, [
                ('upload_date', 'TEXT')
            ])
            conn.commit()
            print(f"Table {table_name} created in temp_bank_outbound.db")

        with self.get_connection('temp_tax_payment.db') as conn:
            cursor = conn.cursor()
            table_name = f"tax_receipt_{safe_id}"
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS "{table_name}" (
                    tax_receipt_id TEXT PRIMARY KEY,
                    date TEXT,
                    amount REAL,
                    currency TEXT,
                    upload_date TEXT
                )
            ''')
            self._add_missing_columns(conn, table_name, [
                ('upload_date', 'TEXT')
            ])
            conn.commit()
            print(f"Table {table_name} created in temp_tax_payment.db")

if __name__ == "__main__":
    manager = DBManager()
    manager.init_company_db()
    manager.init_suppliers_db()
    manager.init_employee_db()
    manager.init_transaction_dbs()
    print("All databases established.")
