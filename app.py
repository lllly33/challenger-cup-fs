from flask import Flask, render_template, request, send_file
import os
import uuid
import shutil
from src.write.writehdf5 import parse_and_store_hdf5_metadata
from src.api_service import get_hdf5_files_from_db, get_hdf5_latlon_data, find_and_crop_hdf5, get_hdf5_variables_from_db
from flask import jsonify # 导入 jsonify
from multiprocessing import Process, Manager, Queue # 导入 multiprocessing 模块
import time

app = Flask(__name__)

# 任务队列和任务状态存储
manager = Manager()
task_queue = Queue()
task_statuses = manager.dict() # 用于存储任务状态和结果

UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

JUICEFS_MOUNT_POINT = '/mnt/myjfs'  # 请确认路径正确

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
                else:
                    raise ValueError(f"未知任务类型: {task_type}")
                task_statuses[task_id] = {'status': 'COMPLETED', 'message': '任务完成', 'result': output_path}
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

@app.route('/api/hdf5_variables/<int:file_id>')
def get_file_variables(file_id):
    print(f"[DEBUG] get_file_variables called for file_id: {file_id}")
    variables = get_hdf5_variables_from_db(file_id)
    if variables:
        print(f"[DEBUG] get_file_variables returning success for file_id: {file_id}, variables: {variables}")
        return jsonify({"status": "success", "data": variables}), 200
    else:
        print(f"[DEBUG] get_file_variables returning success for file_id: {file_id}, no variables found.")
        return jsonify({"status": "success", "data": [], "message": "未找到任何变量"}), 200

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

        unique_filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        print(f"[DEBUG] 准备保存上传文件到临时路径: {temp_filepath}")
        file.save(temp_filepath)
        print(f"[DEBUG] 文件已保存到临时路径")

        jfs_filepath = os.path.join(JUICEFS_MOUNT_POINT, unique_filename)
        print(f"[DEBUG] 目标JuiceFS路径: {jfs_filepath}")

        # 复制文件到JuiceFS挂载点
        shutil.copy(temp_filepath, jfs_filepath)
        print(f"[DEBUG] 文件已复制到JuiceFS挂载点")

        # 删除临时文件
        os.remove(temp_filepath)
        print(f"[DEBUG] 临时文件已删除: {temp_filepath}")

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
        file_id = data.get('file_id')
        var_name = data.get('var_name')
        resolution = float(data.get('resolution'))
        lon_min = data.get('lon_min')
        lon_max = data.get('lon_max')
        lat_min = data.get('lat_min')
        lat_max = data.get('lat_max')
        layer_min = data.get('layer_min')
        layer_max = data.get('layer_max')

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
        return jsonify(status), 200
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
