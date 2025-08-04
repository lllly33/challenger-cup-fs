from flask import Flask, render_template, request, redirect, url_for
import os
import uuid
import shutil # 新增导入
from src.write.writehdf5 import parse_and_store_hdf5_metadata

app = Flask(__name__)

# 配置上传文件目录
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# JuiceFS 挂载点，根据您的实际情况修改
JUICEFS_MOUNT_POINT = '/mnt/jfs'

@app.route('/')
def index():
    """渲染主页"""
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return "没有文件部分", 400
    file = request.files['file']
    if file.filename == '':
        return "没有选择文件", 400
    if file:
        # 生成唯一文件名，防止冲突
        unique_filename = str(uuid.uuid4()) + os.path.splitext(file.filename)[1]
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(temp_filepath)

        # 将文件移动到JuiceFS挂载点
        jfs_filepath = os.path.join(JUICEFS_MOUNT_POINT, unique_filename)
        try:
            shutil.move(temp_filepath, jfs_filepath) # 将 os.rename 替换为 shutil.move
            # 调用元数据入库函数
            success, file_id = parse_and_store_hdf5_metadata(jfs_filepath)
            if success:
                return f"文件 {unique_filename} 上传并入库成功，文件ID: {file_id}"
            else:
                # 如果入库失败，尝试删除JuiceFS上的文件
                if os.path.exists(jfs_filepath):
                    os.remove(jfs_filepath)
                return "文件上传成功，但元数据入库失败", 500
        except Exception as e: # 捕获更广泛的异常
            # 如果移动失败，删除临时文件
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)
            return f"文件移动到JuiceFS失败: {e}", 500

    return "未知错误", 500

import argparse

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run the Flask app.')
    parser.add_argument('--port', type=int, default=5000,
                        help='Port number to run the Flask app on.')
    args = parser.parse_args()
    app.run(host='0.0.0.0', port=args.port, debug=True)