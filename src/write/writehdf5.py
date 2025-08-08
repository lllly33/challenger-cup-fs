import h5py
import psycopg2
import os
import numpy as np
from datetime import datetime
import traceback
from config import DB_HOST, DB_NAME, DB_USER, DB_PASSWORD

# PostgreSQL 数据库连接参数



def insert_hdf5_file_metadata(cursor, file_name, file_path):
    """插入 HDF5 文件信息到 hdf5_files 表，并返回新插入的 file_id。"""
    insert_sql = "INSERT INTO hdf5_files (file_name, file_path) VALUES (%s, %s) RETURNING id;"
    cursor.execute(insert_sql, (file_name, file_path))
    file_id = cursor.fetchone()[0]
    return file_id


def insert_hdf5_group_metadata(cursor, file_id, name, full_path, parent_path):
    """插入 HDF5 Group 信息到 hdf5_groups 表。"""
    insert_sql = "INSERT INTO hdf5_groups (file_id, name, full_path, parent_path) VALUES (%s, %s, %s, %s);"
    cursor.execute(insert_sql, (file_id, name, full_path, parent_path))


def insert_hdf5_dataset_metadata(cursor, file_id, name, full_path, parent_path, dataset):
    """插入 HDF5 Dataset 信息到 hdf5_datasets 表。"""
    shape = str(dataset.shape) if dataset.shape else None
    dtype = str(dataset.dtype) if dataset.dtype else None
    chunks = str(dataset.chunks) if dataset.chunks else None
    compression = dataset.compression
    compression_opts = str(dataset.compression_opts) if dataset.compression_opts is not None else None
    fill_value = str(dataset.fillvalue) if dataset.fillvalue is not None else None

    insert_sql = """
        INSERT INTO hdf5_datasets (file_id, name, full_path, parent_path, shape, dtype, chunks, compression, compression_opts, fill_value)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """
    cursor.execute(insert_sql, (
    file_id, name, full_path, parent_path, shape, dtype, chunks, compression, compression_opts, fill_value))


def insert_hdf5_attribute_metadata(cursor, file_id, parent_path, attr_name, attr_value):
    """插入 HDF5 Attribute 信息到 hdf5_attributes 表。"""
    import base64

    def safe_decode(val):
        if isinstance(val, bytes):
            try:
                return val.decode('utf-8')
            except UnicodeDecodeError:
                return f"base64:{base64.b64encode(val).decode('ascii')}"
        return str(val)

    # 针对 numpy 数组
    if isinstance(attr_value, np.ndarray):
        if attr_value.dtype.type is np.bytes_ or attr_value.dtype == object:
            value_text = [safe_decode(v) for v in attr_value]
        else:
            value_text = str(attr_value)
    # 针对列表/元组
    elif isinstance(attr_value, (list, tuple)):
        value_text = [safe_decode(v) for v in attr_value]
    else:
        value_text = safe_decode(attr_value)

    is_array = isinstance(attr_value, (list, tuple, np.ndarray))

    # array_length
    if isinstance(attr_value, np.ndarray):
        array_length = int(attr_value.size) if attr_value.ndim > 0 else None
    elif isinstance(attr_value, (list, tuple)):
        array_length = len(attr_value)
    else:
        array_length = None

    # dtype
    if isinstance(attr_value, np.ndarray):
        dtype = f"numpy.{attr_value.dtype}"
    elif isinstance(attr_value, bytes):
        dtype = "bytes"
    elif isinstance(attr_value, str):
        dtype = "string"
    elif isinstance(attr_value, (list, tuple)):
        dtype = f"{type(attr_value).__name__}[{len(attr_value)}]"
    else:
        dtype = str(type(attr_value).__name__)

    # str_length
    if isinstance(attr_value, str):
        str_length = len(attr_value)
    elif isinstance(attr_value, bytes):
        str_length = len(attr_value)
    elif isinstance(attr_value, np.ndarray) and attr_value.dtype.type in [np.string_, np.unicode_]:
        str_length = int(attr_value.size)
    else:
        str_length = None

    # padding
    if isinstance(attr_value, (str, bytes)) or (isinstance(attr_value, np.ndarray) and attr_value.dtype.type in [np.string_, np.unicode_]):
        padding = "H5T_STR_NULLTERM"
    else:
        padding = None

    # cset
    if isinstance(attr_value, str) or (isinstance(attr_value, np.ndarray) and attr_value.dtype.type == np.unicode_):
        cset = "H5T_CSET_UTF8"
    elif isinstance(attr_value, bytes) or (isinstance(attr_value, np.ndarray) and attr_value.dtype.type == np.string_):
        cset = "H5T_CSET_ASCII"
    else:
        cset = None

    # 调试输出
    insert_sql = """
        INSERT INTO hdf5_attributes (file_id, parent_path, name, value, is_array, array_length, dtype, str_length, padding, cset)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
    """
    cursor.execute(insert_sql, (
    file_id, parent_path, attr_name, value_text, is_array, array_length, dtype, str_length, padding, cset))

def parse_and_store_hdf5_metadata(hdf5_file_path):
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()

        file_name = os.path.basename(hdf5_file_path)
        print(f"[DEBUG] 连接数据库成功，开始处理文件 {file_name}")

        file_id = insert_hdf5_file_metadata(cur, file_name, hdf5_file_path)
        print(f"[DEBUG] 插入文件元数据，file_id={file_id}")

        with h5py.File(hdf5_file_path, 'r') as hf:
            def visitor_func(name, obj):
                full_path = "/" + name
                parent_path = os.path.dirname(full_path)
                if parent_path == "/":
                    parent_path = "/"

                print(f"[DEBUG] 访问组/数据集/属性: {full_path} 类型: {type(obj)}")

                if isinstance(obj, h5py.Group):
                    insert_hdf5_group_metadata(cur, file_id, obj.name.split('/')[-1], full_path, parent_path)
                    for attr_name, attr_value in obj.attrs.items():
                        insert_hdf5_attribute_metadata(cur, file_id, full_path, attr_name, attr_value)

                elif isinstance(obj, h5py.Dataset):
                    insert_hdf5_dataset_metadata(cur, file_id, obj.name.split('/')[-1], full_path, parent_path, obj)
                    for attr_name, attr_value in obj.attrs.items():
                        insert_hdf5_attribute_metadata(cur, file_id, full_path, attr_name, attr_value)

            hf.visititems(visitor_func)

        conn.commit()
        return True, file_id

    except Exception as e:
        print(f"[ERROR] 处理文件时发生异常: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            conn.rollback()
        return False, None

    finally:
        if conn:
            cur.close()
            conn.close()


if __name__ == "__main__":
    HDF5_FILE_TO_PROCESS = '/Users/crocotear/Documents/挑战者杯/data/hdf5/2A.GPM.Ku.V9-20211125.20230101-S231026-E004258.050253.V07A.HDF5'
    parse_and_store_hdf5_metadata(HDF5_FILE_TO_PROCESS)
