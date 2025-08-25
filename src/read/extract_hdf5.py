import psycopg2
import h5py
import numpy as np
import json
import os
from datetime import datetime
from config import DB_HOST, DB_NAME, DB_USER, DB_PASSWORD


def find_hdf5_files_by_path(search_path):
    """根据路径查找包含该路径的HDF5文件"""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cur = conn.cursor()

        # 查找包含指定路径的文件
        cur.execute("""
            SELECT DISTINCT f.id, f.file_name, f.file_path, f.created_at
            FROM hdf5_files f
            JOIN hdf5_groups g ON f.id = g.file_id
            WHERE g.full_path LIKE %s
            UNION
            SELECT DISTINCT f.id, f.file_name, f.file_path, f.created_at
            FROM hdf5_files f
            JOIN hdf5_datasets d ON f.id = d.file_id
            WHERE d.full_path LIKE %s
            ORDER BY created_at DESC
        """, (f"%{search_path}%", f"%{search_path}%"))

        files = cur.fetchall()
        cur.close()
        conn.close()

        return [{'id': f[0], 'name': f[1], 'path': f[2], 'created_at': f[3]} for f in files]

    except Exception as e:
        print(f"❌ 查找文件失败: {e}")
        return []


def extract_hdf5_subset(file_id, target_path, output_file):
    """从数据库中提取指定路径的HDF5子集并创建新文件"""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cur = conn.cursor()

        # 获取原始文件信息
        cur.execute("SELECT file_name, file_path FROM hdf5_files WHERE id = %s", (file_id,))
        file_info = cur.fetchone()
        if not file_info:
            print(f"❌ 文件ID {file_id} 不存在")
            return False

        original_file_name = file_info[0]
        original_file_path = file_info[1]

        print(f"📖 正在从文件 {original_file_name} 提取路径: {target_path}")

        # 检查原始文件是否存在
        if not os.path.exists(original_file_path):
            print(f"❌ 原始文件不存在: {original_file_path}")
            return False

        # 从原始HDF5文件读取数据
        with h5py.File(original_file_path, 'r') as src_file:
            # 创建新的HDF5文件
            with h5py.File(output_file, 'w') as dst_file:

                # 递归复制指定路径及其子路径
                def copy_path(src_path, dst_path):
                    """递归复制路径"""
                    if src_path in src_file:
                        src_obj = src_file[src_path]

                        if isinstance(src_obj, h5py.Group):
                            # 复制Group
                            if dst_path not in dst_file:
                                dst_file.create_group(dst_path)

                            # 复制Group的属性
                            for attr_name, attr_value in src_obj.attrs.items():
                                dst_file[dst_path].attrs[attr_name] = attr_value

                            # 递归复制子对象
                            for key in src_obj.keys():
                                child_src_path = f"{src_path}/{key}"
                                child_dst_path = f"{dst_path}/{key}"
                                copy_path(child_src_path, child_dst_path)

                        elif isinstance(src_obj, h5py.Dataset):
                            # 复制Dataset
                            dst_file.create_dataset(dst_path, data=src_obj)

                            # 复制Dataset的属性
                            for attr_name, attr_value in src_obj.attrs.items():
                                dst_file[dst_path].attrs[attr_name] = attr_value

                # 开始复制
                copy_path(target_path, target_path)

        print(f"✅ 提取完成！新文件: {output_file}")
        return True

    except Exception as e:
        print(f"❌ 提取失败: {e}")
        return False


def extract_hdf5_by_path(search_path, output_dir="extracted"):
    """根据路径提取HDF5文件"""
    print(f"🔍 搜索包含路径 '{search_path}' 的HDF5文件...")

    # 查找包含该路径的文件
    files = find_hdf5_files_by_path(search_path)

    if not files:
        print(f"❌ 没有找到包含路径 '{search_path}' 的HDF5文件")
        return

    print(f"📋 找到 {len(files)} 个文件:")
    for i, file_info in enumerate(files):
        print(f"  {i + 1}. {file_info['name']} (ID: {file_info['id']})")
        print(f"     原始路径: {file_info['path']}")

    # 创建输出目录
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"📁 创建输出目录: {output_dir}")

    # 提取每个文件
    for i, file_info in enumerate(files):
        print(f"\n🔄 正在处理文件 {i + 1}/{len(files)}: {file_info['name']}")

        # 生成输出文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_path = search_path.replace('/', '_').replace('\\', '_').strip('_')
        output_file = os.path.join(output_dir, f"{file_info['name']}_{safe_path}_{timestamp}.h5")

        # 提取文件
        success = extract_hdf5_subset(file_info['id'], search_path, output_file)

        if success:
            print(f"✅ 文件已保存: {output_file}")
        else:
            print(f"❌ 文件提取失败: {file_info['name']}")


def list_available_paths(file_id):
    """列出指定文件中所有可用的路径"""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cur = conn.cursor()

        # 获取所有Groups路径
        cur.execute("""
            SELECT full_path FROM hdf5_groups
            WHERE file_id = %s
            ORDER BY full_path
        """, (file_id,))
        group_paths = [row[0] for row in cur.fetchall()]

        # 获取所有Datasets路径
        cur.execute("""
            SELECT full_path FROM hdf5_datasets
            WHERE file_id = %s
            ORDER BY full_path
        """, (file_id,))
        dataset_paths = [row[0] for row in cur.fetchall()]

        cur.close()
        conn.close()

        all_paths = sorted(set(group_paths + dataset_paths))
        return all_paths

    except Exception as e:
        print(f"❌ 获取路径列表失败: {e}")
        return []


def main():
    """主函数"""
    print("🔧 HDF5 文件提取工具")
    print("=" * 40)

    # 获取所有文件
    files = find_hdf5_files_by_path("")  # 空字符串会匹配所有文件
    if not files:
        print("❌ 数据库中没有找到HDF5文件")
        return

    print("📋 可用文件:")
    for i, file_info in enumerate(files):
        print(f"  {i + 1}. {file_info['name']} (ID: {file_info['id']})")

    try:
        # 获取用户输入
        choice = int(input("\n请选择文件编号 (1-{}): ".format(len(files))))
        if choice < 1 or choice > len(files):
            print("❌ 无效选择")
            return

        selected_file = files[choice - 1]

        # 列出可用路径
        print(f"\n📂 文件 '{selected_file['name']}' 中的可用路径:")
        paths = list_available_paths(selected_file['id'])
        for path in paths:
            print(f"  - {path}")

        # 获取目标路径
        target_path = input(f"\n🔍 请输入要提取的路径 (例如: /FS/CSF/binBBBottom): ").strip()
        if not target_path:
            print("❌ 路径不能为空")
            return

        # 检查路径是否存在
        if target_path not in paths:
            print(f"⚠️  警告: 路径 '{target_path}' 在文件中不存在")
            print("可用的路径:")
            for path in paths:
                if target_path in path:
                    print(f"  - {path}")
            continue_choice = input("是否继续? (y/n): ").strip().lower()
            if continue_choice != 'y':
                return

        # 提取文件
        extract_hdf5_by_path(target_path)

    except ValueError:
        print("❌ 请输入有效的数字")
    except KeyboardInterrupt:
        print("\n👋 再见!")
    except Exception as e:
        print(f"❌ 发生错误: {e}")


if __name__ == "__main__":
    main()