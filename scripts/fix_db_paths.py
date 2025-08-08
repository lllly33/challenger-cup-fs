import psycopg2
import os

# --- 数据库连接参数 (与项目其他部分保持一致) ---
DB_HOST = "localhost"
DB_NAME = "juicefs"
DB_USER = "juiceuser"
DB_PASSWORD = "0333"

def fix_paths():
    """
    连接到数据库，并将 hdf5_files 表中不正确的 /mnt/jfs/ 路径修正为 /mnt/myjfs/。
    """
    conn = None
    updated_count = 0
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()

        # 使用 SQL 的 REPLACE 函数直接替换路径前缀
        # WHERE 子句确保我们只修改那些错误的行
        update_query = """
            UPDATE hdf5_files
            SET file_path = REPLACE(file_path, '/mnt/jfs/', '/mnt/myjfs/')
            WHERE file_path LIKE '/mnt/jfs/%%';
        """
        
        print("准备执行数据库路径修复...")
        cur.execute(update_query)
        
        # 获取受影响的行数
        updated_count = cur.rowcount
        
        conn.commit()
        
        if updated_count > 0:
            print(f"成功！共修复了 {updated_count} 条文件路径记录。")
        else:
            print("数据库中未发现需要修复的路径。")

    except Exception as e:
        print(f"执行数据库路径修复时发生错误: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            cur.close()
            conn.close()

if __name__ == "__main__":
    fix_paths()
