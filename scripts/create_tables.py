
import psycopg2
from psycopg2 import sql

# PostgreSQL 数据库连接参数
DB_HOST = "XXXX"
DB_NAME = "XXXX"
DB_USER = "XXXX"
DB_PASSWORD = "XXXX"

def create_tables():
    """
    在 PostgreSQL 数据库中创建所需的表。
    """
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()

        # 创建 hdf5_files 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hdf5_files (
                id SERIAL PRIMARY KEY,
                file_name VARCHAR(255) NOT NULL,
                file_path TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 创建 hdf5_groups 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hdf5_groups (
                id SERIAL PRIMARY KEY,
                file_id INTEGER NOT NULL REFERENCES hdf5_files(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                full_path TEXT NOT NULL,
                parent_path TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 创建 hdf5_datasets 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hdf5_datasets (
                id SERIAL PRIMARY KEY,
                file_id INTEGER NOT NULL REFERENCES hdf5_files(id) ON DELETE CASCADE,
                name VARCHAR(255) NOT NULL,
                full_path TEXT NOT NULL,
                parent_path TEXT,
                shape TEXT,
                dtype TEXT,
                chunks TEXT,
                compression TEXT,
                compression_opts TEXT,
                fill_value TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 创建 hdf5_attributes 表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hdf5_attributes (
                id SERIAL PRIMARY KEY,
                file_id INTEGER NOT NULL REFERENCES hdf5_files(id) ON DELETE CASCADE,
                parent_path TEXT NOT NULL,
                name VARCHAR(255) NOT NULL,
                value TEXT,
                is_array BOOLEAN,
                array_length INTEGER,
                dtype TEXT,
                str_length INTEGER,
                padding TEXT,
                cset TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        conn.commit()
        print("Tables created successfully.")

    except Exception as e:
        print(f"Error creating tables: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            cur.close()
            conn.close()

if __name__ == "__main__":
    create_tables()
