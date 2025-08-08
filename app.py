from flask import Flask, render_template, request, send_file
import os
import uuid
import shutil
from datetime import datetime # 导入 datetime 模块
from src.write.writehdf5 import parse_and_store_hdf5_metadata
from src.api_service import get_hdf5_files_from_db, get_hdf5_latlon_data, find_and_crop_hdf5, get_hdf5_variables_from_db, get_hdf5_groups_from_db, get_hdf5_internal_paths, perform_hdf5_subset_extraction
from flask import jsonify # 导入 jsonify
from config import JUICEFS_MOUNT_POINT
from multiprocessing import Process, Manager, Queue # 导入 multiprocessing 模块
import time

app = Flask(__name__)

# 任务队列和任务状态存储
manager = Manager()
task_queue = Queue()
task_statuses = manager.dict() # 用于存储任务状态和结果

UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER



# 后台工作函数
def worker(task_queue, task_statuses):
    while True:
        if not task_queue.empty():
            task_id, task_data = task_queue.get()
            task_statuses[task_id] = {'status': 'RUNNING', 'message': '任务正在执行...'}
            print(f"[WORKER] 开始执行任务: {task_id}")
            try:
                task_type = task_data.get('task_type')

                if task_type == 'crop':
                    file_name = task_data['file_name']
                    lat_min = task_data['lat_min']
                    lat_max = task_data['lat_max']
                    lon_min = task_data['lon_min']
                    lon_max = task_data['lon_max']
                    output_path = find_and_crop_hdf5(
                        file_name=file_name,
                        lat_min=lat_min,
                        lat_max=lat_max,
                        lon_min=lon_min,
                        lon_max=lon_max
                    )
                elif task_type == 'interpolate':
                    file_id = task_data['file_id']
                    var_name = task_data['var_name']
                    resolution = task_data['resolution']
                    lon_min = task_data.get('lon_min')
                    lon_max = task_data.get('lon_max')
                    lat_min = task_data.get('lat_min')
                    lat_max = task_data.get('lat_max')
                    layer_min = task_data.get('layer_min')
                    layer_max = task_data.get('layer_max')

                    from src.api_service import perform_interpolation # 导入插值函数
                    output_path = perform_interpolation(
                        file_id=file_id,
                        var_name=var_name,
                        resolution=resolution,
                        lon_min=lon_min,
                        lon_max=lon_max,
                        lat_min=lat_min,
                        lat_max=lat_max,
                        layer_min=layer_min,
                        layer_max=layer_max
                    )
                elif task_type == 'extract_subset':
                    file_id = task_data['file_id']
                    target_path = task_data['target_path']
                    output_filename = task_data.get('output_filename')
                    output_path = perform_hdf5_subset_extraction(
                        file_id=file_id,
                        target_path=target_path,
                        output_filename=output_filename
                    )
                else:
                    raise ValueError(f"未知任务类型: {task_type}")
                task_statuses[task_id] = {'status': 'COMPLETED', 'message': '任务完成', 'result': output_path, 'task_type': task_type}
                print(f"[WORKER] 任务 {task_id} 完成，结果: {output_path}")
            except Exception as e:
                task_statuses[task_id] = {'status': 'FAILED', 'message': f'任务失败: {e}'}
                print(f"[WORKER] 任务 {task_id} 失败: {e}")
                import traceback
                traceback.print_exc()
        time.sleep(1) # 避免CPU空转

# 启动时确保上传目录存在
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])
    print(f"[DEBUG] 创建上传目录: {app.config['UPLOAD_FOLDER']}")
else:
    print(f"[DEBUG] 上传目录已存在: {app.config['UPLOAD_FOLDER']}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/hdf5_files')
def list_hdf5_files():
    files = get_hdf5_files_from_db()
    return jsonify(files)

@app.route('/api/hdf5_file_latlon/<int:file_id>')
def get_file_latlon(file_id):
    latlon_data = get_hdf5_latlon_data(file_id)
    if latlon_data:
        return jsonify({"status": "success", "data": latlon_data}), 200
    else:
        return jsonify({"status": "error", "message": "无法获取文件经纬度数据"}), 404

@app.route('/api/hdf5_groups/<int:file_id>')
def get_file_groups(file_id):
    groups = get_hdf5_groups_from_db(file_id)
    return jsonify({"status": "success", "data": groups})

@app.route('/api/hdf5_variables/<int:file_id>')
def get_file_variables(file_id):
    # 从URL查询参数中获取group_path，例如: /api/hdf5_variables/1?group=/FS/Swath
    group_path = request.args.get('group', None)
    print(f"[DEBUG] get_file_variables called for file_id: {file_id}, group: {group_path}")
    variables = get_hdf5_variables_from_db(file_id, group_path)
    # 注意：这里不再对variables是否为空做特殊判断，直接返回数据库查询结果
    print(f"[DEBUG] get_file_variables returning success for file_id: {file_id}, variables: {variables}")
    return jsonify({"status": "success", "data": variables}), 200

@app.route('/api/hdf5_internal_paths/<int:file_id>')
def list_hdf5_internal_paths(file_id):
    """
    获取指定HDF5文件内部的所有Group和Dataset路径。
    """
    try:
        paths = get_hdf5_internal_paths(file_id)
        return jsonify({"status": "success", "data": paths}), 200
    except Exception as e:
        print(f"[ERROR] 获取HDF5内部路径失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"获取HDF5内部路径失败: {e}"}), 500

@app.route('/api/extract_hdf5_subset', methods=['POST'])
def extract_hdf5_subset_api():
    """
    提交HDF5子集提取任务。
    """
    try:
        data = request.get_json()
        file_id = data.get('file_id')
        target_path = data.get('target_path')
        output_filename = data.get('output_filename')

        if not file_id or not target_path:
            return jsonify({"status": "error", "message": "缺少必要参数: file_id, target_path"}), 400

        task_id = str(uuid.uuid4())
        task_data = {
            'task_type': 'extract_subset',
            'file_id': file_id,
            'target_path': target_path,
            'output_filename': output_filename
        }
        task_queue.put((task_id, task_data))
        task_statuses[task_id] = {'status': 'PENDING', 'message': '子集提取任务已提交，等待处理'}

        return jsonify({"status": "success", "message": "子集提取任务已提交", "task_id": task_id}), 202

    except Exception as e:
        print(f"[ERROR] 子集提取请求处理失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"子集提取请求处理失败: {e}"}), 500

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            print("[DEBUG] 请求中无文件部分")
            return "没有文件部分", 400
        file = request.files['file']
        if file.filename == '':
            print("[DEBUG] 未选择文件")
            return "没有选择文件", 400

        # 获取用户提供的重命名，并去除首尾空格
        new_filename_base = request.form.get('new_filename', '').strip()
        original_filename_base = os.path.splitext(file.filename)[0]
        file_extension = os.path.splitext(file.filename)[1]

        # 决定最终的文件名
        if new_filename_base:
            # 用户提供了重命名
            unique_filename = new_filename_base + file_extension
        else:
            # 用户未提供，使用原名+时间戳
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            unique_filename = f"{original_filename_base}_{timestamp}{file_extension}"

        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], str(uuid.uuid4()) + file_extension) # 临时文件仍然使用UUID防止冲突
        print(f"[DEBUG] 准备保存上传文件到临时路径: {temp_filepath}")
        file.save(temp_filepath)
        print(f"[DEBUG] 文件已保存到临时路径")

        jfs_filepath = os.path.join(JUICEFS_MOUNT_POINT, unique_filename)
        print(f"[DEBUG] 目标JuiceFS路径: {jfs_filepath}")

        # 检查目标文件是否已存在，避免覆盖
        if os.path.exists(jfs_filepath):
            os.remove(temp_filepath) # 清理临时文件
            return f"文件上传失败：JuiceFS中已存在同名文件 '{unique_filename}'。请使用不同的名称重命名。", 409 # 409 Conflict

        # 移动文件到JuiceFS挂载点 (使用shutil.move更高效)
        shutil.move(temp_filepath, jfs_filepath)
        print(f"[DEBUG] 文件已移动到JuiceFS挂载点")

        # 调用元数据入库
        print(f"[DEBUG] 开始调用元数据入库函数，处理文件: {jfs_filepath}")
        success, file_id = parse_and_store_hdf5_metadata(jfs_filepath)
        if success:
            print(f"[DEBUG] 元数据入库成功，文件ID: {file_id}")
            return f"文件 {unique_filename} 上传并入库成功，文件ID: {file_id}"
        else:
            print(f"[ERROR] 元数据入库失败，准备删除JuiceFS上的文件: {jfs_filepath}")
            if os.path.exists(jfs_filepath):
                os.remove(jfs_filepath)
                print(f"[DEBUG] JuiceFS上的文件已删除")
            return "文件上传成功，但元数据入库失败", 500

    except Exception as e:
        print(f"[ERROR] 上传或入库过程中发生异常: {e}")
        import traceback
        traceback.print_exc()
        # 异常时清理临时文件和JuiceFS文件
        try:
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)
                print(f"[DEBUG] 异常发生，删除临时文件: {temp_filepath}")
            if 'jfs_filepath' in locals() and os.path.exists(jfs_filepath):
                os.remove(jfs_filepath)
                print(f"[DEBUG] 异常发生，删除JuiceFS文件: {jfs_filepath}")
        except Exception as cleanup_e:
            print(f"[ERROR] 异常处理中删除文件失败: {cleanup_e}")
        return f"文件上传或处理失败: {e}", 500

    return "未知错误", 500


from src.api_service import find_and_crop_hdf5 # 导入裁剪函数

@app.route('/api/crop', methods=['POST'])
def crop_hdf5_file():
    try:
        data = request.get_json()
        file_id = data.get('file_id')
        lat_min = float(data.get('lat_min'))
        lat_max = float(data.get('lat_max'))
        lon_min = float(data.get('lon_min'))
        lon_max = float(data.get('lon_max'))

        # 根据 file_id 从数据库获取 file_name
        files = get_hdf5_files_from_db()
        file_name = None
        for f in files:
            if str(f['id']) == str(file_id):
                file_name = f['file_name']
                break

        if not file_name:
            return jsonify({"status": "error", "message": "未找到对应的HDF5文件"}), 404

        task_id = str(uuid.uuid4())
        task_data = {
            'task_type': 'crop',
            'file_name': file_name,
            'lat_min': lat_min,
            'lat_max': lat_max,
            'lon_min': lon_min,
            'lon_max': lon_max
        }
        task_queue.put((task_id, task_data))
        task_statuses[task_id] = {'status': 'PENDING', 'message': '任务已提交，等待处理'}

        return jsonify({"status": "success", "message": "裁剪任务已提交", "task_id": task_id}), 202 # 202 Accepted

    except Exception as e:
        print(f"[ERROR] 裁剪请求处理失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"裁剪请求处理失败: {e}"}), 500

@app.route('/api/interpolate', methods=['POST'])
def interpolate_hdf5_file():
    try:
        data = request.get_json()
        
        # 安全地转换所有可能为空的数值参数
        def safe_float(value):
            if value is None or value == '':
                return None
            try:
                return float(value)
            except (ValueError, TypeError):
                return None # 如果转换失败，也返回None

        def safe_int(value):
            if value is None or value == '':
                return None
            try:
                return int(value)
            except (ValueError, TypeError):
                return None

        file_id = data.get('file_id')
        var_name = data.get('var_name')
        resolution = safe_float(data.get('resolution'))
        lon_min = safe_float(data.get('lon_min'))
        lon_max = safe_float(data.get('lon_max'))
        lat_min = safe_float(data.get('lat_min'))
        lat_max = safe_float(data.get('lat_max'))
        layer_min = safe_int(data.get('layer_min'))
        layer_max = safe_int(data.get('layer_max'))

        if file_id is None or var_name is None or resolution is None:
            return jsonify({"status": "error", "message": "缺少必要参数: file_id, var_name, resolution"}), 400

        task_id = str(uuid.uuid4())
        task_data = {
            'task_type': 'interpolate',
            'file_id': file_id,
            'var_name': var_name,
            'resolution': resolution,
            'lon_min': lon_min,
            'lon_max': lon_max,
            'lat_min': lat_min,
            'lat_max': lat_max,
            'layer_min': layer_min,
            'layer_max': layer_max
        }
        task_queue.put((task_id, task_data))
        task_statuses[task_id] = {'status': 'PENDING', 'message': '插值任务已提交，等待处理'}

        return jsonify({"status": "success", "message": "插值任务已提交", "task_id": task_id}), 202 # 202 Accepted

    except Exception as e:
        print(f"[ERROR] 插值请求处理失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"插值请求处理失败: {e}"}), 500

@app.route('/api/status/<task_id>')
def get_task_status(task_id):
    status = task_statuses.get(task_id)
    if status:
        return jsonify(status), 200 # 直接返回整个 status 字典
    else:
        return jsonify({"status": "error", "message": "任务ID不存在"}), 404

@app.route('/download/<task_id>')
def download_file(task_id):
    status = task_statuses.get(task_id)
    if status and status['status'] == 'COMPLETED' and 'result' in status:
        file_path = status['result']
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True, download_name=os.path.basename(file_path))
        else:
            return jsonify({"status": "error", "message": "文件未找到"}), 404
    else:
        return jsonify({"status": "error", "message": "任务未完成或不存在"}), 404


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Run the Flask app.')
    parser.add_argument('--port', type=int, default=5001, help='Port number to run the Flask app on.')
    args = parser.parse_args()
    print(f"[DEBUG] 启动 Flask 服务，端口: {args.port}")

    # 启动后台工作进程
    worker_process = Process(target=worker, args=(task_queue, task_statuses))
    worker_process.daemon = True # 设置为守护进程，主进程退出时子进程也退出
    worker_process.start()
    print("[DEBUG] 后台工作进程已启动")

    app.run(host='0.0.0.0', port=args.port, debug=True)
