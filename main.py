import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
import requests
import re
import logging
from telebot import types
import time
from datetime import datetime, timedelta
import signal
import psutil
import sqlite3
import threading
import base64
import random
import string
from flask import Flask, request, jsonify, send_from_directory, render_template_string, session
from werkzeug.utils import secure_filename
import secrets

# Initialize Flask app for file manager
file_manager_app = Flask(__name__)
file_manager_app.secret_key = secrets.token_hex(16)
file_manager_running = False
file_manager_port = 5000

TOKEN = '8439087531:AAFqRUY2C4k6fGehUKh2E6msg9HQO8K4gPU'
ADMIN_ID = 7064198008
YOUR_USERNAME = '@Pythonfile_host_bot'

bot = telebot.TeleBot(TOKEN)

# Configuration
uploaded_files_dir = 'uploaded_bots'
file_manager_base_url = 'https://b4bfb621-ac52-4697-9809-155a3d6218ac-00-2fbbgwj2mqvfs.pike.replit.dev/'
max_slots = 3  # Maximum projects per user
session_timeout = 15  # Minutes

# Data structures
bot_scripts = {}
stored_tokens = {}
user_subscriptions = {}
user_files = {}
active_users = set()
file_manager_sessions = {}
user_slots = {}  # Track user project slots

bot_locked = False
free_mode = False

# Create necessary directories
if not os.path.exists(uploaded_files_dir):
    os.makedirs(uploaded_files_dir)

def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    
    try:
        # Create tables with proper schema
        tables = [
            '''CREATE TABLE IF NOT EXISTS subscriptions
               (user_id INTEGER PRIMARY KEY, expiry TEXT)''',
            '''CREATE TABLE IF NOT EXISTS user_files
               (user_id INTEGER, file_name TEXT, project_name TEXT, PRIMARY KEY (user_id, project_name))''',
            '''CREATE TABLE IF NOT EXISTS active_users
               (user_id INTEGER PRIMARY KEY)''',
            '''CREATE TABLE IF NOT EXISTS file_manager_sessions
               (user_id INTEGER PRIMARY KEY, url TEXT, username TEXT, password TEXT, expiry TEXT)''',
            '''CREATE TABLE IF NOT EXISTS user_slots
               (user_id INTEGER PRIMARY KEY, slots_used INTEGER DEFAULT 0)'''
        ]
        
        for table in tables:
            c.execute(table)
        
        # Check if we need to migrate existing data
        c.execute("PRAGMA table_info(user_files)")
        columns = [column[1] for column in c.fetchall()]
        
        # If project_name column doesn't exist, add it
        if 'project_name' not in columns:
            c.execute('ALTER TABLE user_files ADD COLUMN project_name TEXT DEFAULT "Main"')
            # Update existing records to have a default project name
            c.execute('UPDATE user_files SET project_name = "Main" WHERE project_name IS NULL')
        
        conn.commit()
        print("✅ Database initialized successfully")
        
    except Exception as e:
        print(f"⚠️ Database initialization error: {e}")
        conn.rollback()
    finally:
        conn.close()

def load_data():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    
    try:
        # Load subscriptions
        c.execute('SELECT * FROM subscriptions')
        for row in c.fetchall():
            if len(row) >= 2:
                user_id, expiry = row[0], row[1]
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
        
        # Load user files and projects
        c.execute('SELECT * FROM user_files')
        for row in c.fetchall():
            if len(row) >= 3:
                user_id, file_name, project_name = row[0], row[1], row[2]
                if user_id not in user_files:
                    user_files[user_id] = {}
                user_files[user_id][project_name] = file_name
        
        # Load active users
        c.execute('SELECT * FROM active_users')
        for row in c.fetchall():
            if len(row) >= 1:
                user_id = row[0]
                active_users.add(user_id)
        
        # Load file manager sessions
        c.execute('SELECT * FROM file_manager_sessions')
        for row in c.fetchall():
            if len(row) >= 5:
                user_id, url, username, password, expiry = row[0], row[1], row[2], row[3], row[4]
                file_manager_sessions[user_id] = {
                    'url': url,
                    'username': username,
                    'password': password,
                    'expiry': datetime.fromisoformat(expiry)
                }
        
        # Load user slots
        c.execute('SELECT * FROM user_slots')
        for row in c.fetchall():
            if len(row) >= 2:
                user_id, slots_used = row[0], row[1]
                user_slots[user_id] = slots_used
                
    except Exception as e:
        print(f"Warning: Error loading data from database: {e}")
        # Continue with empty data structures
        pass
    
    conn.close()

def save_subscription(user_id, expiry):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)', 
              (user_id, expiry.isoformat()))
    conn.commit()
    conn.close()

def save_file_manager_session(user_id, session_data):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO file_manager_sessions 
                 (user_id, url, username, password, expiry) VALUES (?, ?, ?, ?, ?)''', 
              (user_id, session_data['url'], session_data['username'], 
               session_data['password'], session_data['expiry'].isoformat()))
    conn.commit()
    conn.close()

def save_user_project(user_id, file_name, project_name):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO user_files (user_id, file_name, project_name) 
                 VALUES (?, ?, ?)''', (user_id, file_name, project_name))
    conn.commit()
    conn.close()

def update_user_slots(user_id, slots_used):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO user_slots (user_id, slots_used) VALUES (?, ?)', 
              (user_id, slots_used))
    conn.commit()
    conn.close()

try:
    init_db()
    load_data()
    print("✅ Bot initialization completed successfully")
except Exception as e:
    print(f"❌ Bot initialization failed: {e}")
    # Continue with default values

# Helper functions
def generate_password(length=12):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def get_user_slots(user_id):
    return user_slots.get(user_id, 0)

def can_create_project(user_id):
    if free_mode or user_id == ADMIN_ID:
        return True
    return get_user_slots(user_id) < max_slots

# Menu creation functions
def create_main_menu(user_id):
    markup = types.InlineKeyboardMarkup()
    
    # Common buttons
    projects_button = types.InlineKeyboardButton('📂 My Projects', callback_data='my_projects')
    file_manager_button = types.InlineKeyboardButton('🌐 File Manager', callback_data='file_manager')
    contact_button = types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME[1:]}')
    
    if user_id == ADMIN_ID:
        # Admin buttons
        admin_buttons = [
            types.InlineKeyboardButton('📤 Upload File', callback_data='upload'),
            types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
            types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'),
            types.InlineKeyboardButton('📊 Statistics', callback_data='stats'),
            types.InlineKeyboardButton('🔒 Lock Bot', callback_data='lock_bot'),
            types.InlineKeyboardButton('🔓 Unlock Bot', callback_data='unlock_bot'),
            types.InlineKeyboardButton('🔓 Free Mode', callback_data='free_mode'),
            types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast')
        ]
        
        markup.add(projects_button, file_manager_button)
        markup.add(admin_buttons[0], admin_buttons[1])
        markup.add(admin_buttons[2], admin_buttons[3])
        markup.add(admin_buttons[4], admin_buttons[5], admin_buttons[6])
        markup.add(admin_buttons[7])
    else:
        # Regular user buttons
        markup.add(projects_button, file_manager_button)
    
    markup.add(contact_button)
    return markup

def create_projects_menu(user_id):
    markup = types.InlineKeyboardMarkup()
    
    # Add existing projects
    if user_id in user_files:
        for project_name, file_name in user_files[user_id].items():
            btn = types.InlineKeyboardButton(f'📁 {project_name}', callback_data=f'project_{project_name}')
            markup.add(btn)
    
    # Add action buttons
    if can_create_project(user_id):
        new_project_btn = types.InlineKeyboardButton('➕ New Project', callback_data='new_project')
        markup.add(new_project_btn)
    
    back_btn = types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main')
    markup.add(back_btn)
    
    return markup

def create_project_menu(user_id, project_name):
    markup = types.InlineKeyboardMarkup()
    
    # Project actions
    manage_files_btn = types.InlineKeyboardButton('📁 Manage Files', callback_data=f'manage_{project_name}')
    deploy_btn = types.InlineKeyboardButton('🚀 Deploy', callback_data=f'deploy_{project_name}')
    delete_btn = types.InlineKeyboardButton('🗑️ Delete', callback_data=f'delete_{project_name}')
    
    markup.add(manage_files_btn)
    markup.add(deploy_btn, delete_btn)
    
    # Back button
    back_btn = types.InlineKeyboardButton('🔙 Back to Projects', callback_data='back_to_projects')
    markup.add(back_btn)
    
    return markup

# Flask File Manager Routes
@file_manager_app.route('/')
def file_manager_home():
    return render_template_string('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Python File Manager</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .container {
            background: white;
            border-radius: 15px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
            width: 90%;
            max-width: 1200px;
            min-height: 80vh;
            overflow: hidden;
        }

        .header {
            background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
            color: white;
            padding: 20px;
            text-align: center;
        }

        .login-form {
            padding: 40px;
            text-align: center;
        }

        .login-form input {
            width: 100%;
            max-width: 300px;
            padding: 15px;
            margin: 10px;
            border: 2px solid #ddd;
            border-radius: 8px;
            font-size: 16px;
        }

        .login-form button {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 8px;
            font-size: 16px;
            cursor: pointer;
            margin: 10px;
        }

        .file-manager {
            display: none;
            padding: 20px;
        }

        .toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
        }

        .file-list {
            border: 1px solid #ddd;
            border-radius: 8px;
            overflow: hidden;
        }

        .file-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 15px;
            border-bottom: 1px solid #eee;
            transition: background 0.3s;
        }

        .file-item:hover {
            background: #f5f5f5;
        }

        .file-actions {
            display: flex;
            gap: 10px;
        }

        .btn {
            padding: 8px 15px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
        }

        .btn-primary { background: #007bff; color: white; }
        .btn-danger { background: #dc3545; color: white; }
        .btn-success { background: #28a745; color: white; }
        .btn-warning { background: #ffc107; color: black; }

        .upload-area {
            border: 2px dashed #ddd;
            border-radius: 8px;
            padding: 40px;
            text-align: center;
            margin: 20px 0;
            transition: border-color 0.3s;
        }

        .upload-area.dragover {
            border-color: #007bff;
            background: #f8f9ff;
        }

        .hidden {
            display: none;
        }

        .alert {
            padding: 15px;
            margin: 10px 0;
            border-radius: 5px;
        }

        .alert-success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }

        .alert-error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }

        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
        }

        .modal-content {
            background: white;
            margin: 15% auto;
            padding: 20px;
            border-radius: 8px;
            width: 80%;
            max-width: 500px;
        }

        .code-editor {
            width: 100%;
            height: 400px;
            border: 1px solid #ddd;
            border-radius: 5px;
            padding: 10px;
            font-family: 'Courier New', monospace;
            resize: vertical;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🐍 Python File Manager</h1>
            <p>Manage your Python projects online</p>
        </div>

        <!-- Login Form -->
        <div id="loginForm" class="login-form">
            <h2>Login to File Manager</h2>
            <div id="loginAlert"></div>
            <input type="text" id="username" placeholder="Username" required>
            <input type="password" id="password" placeholder="Password" required>
            <br>
            <button onclick="login()">🔐 Login</button>
        </div>

        <!-- File Manager Interface -->
        <div id="fileManager" class="file-manager">
            <div class="toolbar">
                <div>
                    <span id="welcomeText"></span>
                    <span id="projectPath"></span>
                </div>
                <div>
                    <button class="btn btn-primary" onclick="showUploadArea()">📤 Upload</button>
                    <button class="btn btn-success" onclick="createNewFile()">📄 New File</button>
                    <button class="btn btn-warning" onclick="logout()">🚪 Logout</button>
                </div>
            </div>

            <div id="uploadArea" class="upload-area hidden">
                <p>📁 Drag and drop files here or</p>
                <input type="file" id="fileInput" multiple style="display: none;">
                <button class="btn btn-primary" onclick="document.getElementById('fileInput').click()">Choose Files</button>
            </div>

            <div id="fileList" class="file-list">
                <!-- Files will be loaded here -->
            </div>
        </div>
    </div>

    <!-- File Editor Modal -->
    <div id="fileModal" class="modal">
        <div class="modal-content">
            <h3 id="modalTitle">Edit File</h3>
            <textarea id="codeEditor" class="code-editor" placeholder="File content..."></textarea>
            <div style="margin-top: 15px; text-align: right;">
                <button class="btn btn-success" onclick="saveFile()">💾 Save</button>
                <button class="btn btn-danger" onclick="closeModal()">❌ Cancel</button>
            </div>
        </div>
    </div>

    <script>
        let currentUser = null;
        let currentProject = null;
        let editingFile = null;

        // Login functionality
        async function login() {
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            
            if (!username || !password) {
                showAlert('loginAlert', 'Please enter username and password', 'error');
                return;
            }

            try {
                const response = await fetch('/api/login', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ username, password })
                });

                const result = await response.json();

                if (result.success) {
                    currentUser = result.user;
                    currentProject = result.project;
                    showFileManager();
                    loadFiles();
                } else {
                    showAlert('loginAlert', result.message || 'Invalid credentials', 'error');
                }
            } catch (error) {
                showAlert('loginAlert', 'Connection error. Please try again.', 'error');
            }
        }

        function showFileManager() {
            document.getElementById('loginForm').style.display = 'none';
            document.getElementById('fileManager').style.display = 'block';
            document.getElementById('welcomeText').textContent = `Welcome, ${currentUser}!`;
            document.getElementById('projectPath').textContent = `Project: ${currentProject}`;
        }

        function logout() {
            currentUser = null;
            currentProject = null;
            document.getElementById('loginForm').style.display = 'block';
            document.getElementById('fileManager').style.display = 'none';
            document.getElementById('username').value = '';
            document.getElementById('password').value = '';
            document.getElementById('loginAlert').innerHTML = '';
        }

        // File management
        async function loadFiles() {
            try {
                const response = await fetch(`/api/files/${currentUser}/${currentProject}`);
                const result = await response.json();

                if (result.success) {
                    displayFiles(result.files);
                } else {
                    showAlert('fileAlert', 'Error loading files', 'error');
                }
            } catch (error) {
                showAlert('fileAlert', 'Error loading files', 'error');
            }
        }

        function displayFiles(files) {
            const fileList = document.getElementById('fileList');
            fileList.innerHTML = '';

            if (files.length === 0) {
                fileList.innerHTML = '<div style="padding: 40px; text-align: center; color: #666;">No files found. Upload some files to get started!</div>';
                return;
            }

            files.forEach(file => {
                const fileItem = document.createElement('div');
                fileItem.className = 'file-item';
                
                const fileIcon = file.type === 'directory' ? '📁' : '📄';
                const fileSize = file.type === 'file' ? formatFileSize(file.size) : '';
                
                fileItem.innerHTML = `
                    <div>
                        <span>${fileIcon} ${file.name}</span>
                        ${fileSize ? `<small style="color: #666; margin-left: 10px;">${fileSize}</small>` : ''}
                    </div>
                    <div class="file-actions">
                        ${file.type === 'file' ? `
                            <button class="btn btn-primary" onclick="editFile('${file.name}')">✏️ Edit</button>
                            <button class="btn btn-success" onclick="runFile('${file.name}')">▶️ Run</button>
                        ` : ''}
                        <button class="btn btn-danger" onclick="deleteFile('${file.name}')">🗑️ Delete</button>
                    </div>
                `;
                
                fileList.appendChild(fileItem);
            });
        }

        async function editFile(filename) {
            try {
                const response = await fetch(`/api/file-content/${currentUser}/${currentProject}/${filename}`);
                const result = await response.json();

                if (result.success) {
                    editingFile = filename;
                    document.getElementById('modalTitle').textContent = `Edit ${filename}`;
                    document.getElementById('codeEditor').value = result.content;
                    document.getElementById('fileModal').style.display = 'block';
                } else {
                    alert('Error loading file content');
                }
            } catch (error) {
                alert('Error loading file content');
            }
        }

        async function saveFile() {
            if (!editingFile) return;

            const content = document.getElementById('codeEditor').value;

            try {
                const response = await fetch(`/api/save-file/${currentUser}/${currentProject}/${editingFile}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ content })
                });

                const result = await response.json();

                if (result.success) {
                    closeModal();
                    loadFiles();
                    alert('File saved successfully!');
                } else {
                    alert('Error saving file');
                }
            } catch (error) {
                alert('Error saving file');
            }
        }

        async function runFile(filename) {
            try {
                const response = await fetch(`/api/run-file/${currentUser}/${currentProject}/${filename}`, {
                    method: 'POST'
                });

                const result = await response.json();
                
                if (result.success) {
                    alert(`File executed successfully!\\nOutput:\\n${result.output}`);
                } else {
                    alert(`Error running file:\\n${result.error}`);
                }
            } catch (error) {
                alert('Error running file');
            }
        }

        async function deleteFile(filename) {
            if (!confirm(`Are you sure you want to delete ${filename}?`)) return;

            try {
                const response = await fetch(`/api/delete-file/${currentUser}/${currentProject}/${filename}`, {
                    method: 'DELETE'
                });

                const result = await response.json();

                if (result.success) {
                    loadFiles();
                    alert('File deleted successfully!');
                } else {
                    alert('Error deleting file');
                }
            } catch (error) {
                alert('Error deleting file');
            }
        }

        function closeModal() {
            document.getElementById('fileModal').style.display = 'none';
            editingFile = null;
        }

        function showUploadArea() {
            const uploadArea = document.getElementById('uploadArea');
            uploadArea.classList.toggle('hidden');
        }

        async function createNewFile() {
            const filename = prompt('Enter filename (with .py extension):');
            if (!filename) return;

            if (!filename.endsWith('.py')) {
                alert('Please use .py extension for Python files');
                return;
            }

            try {
                const response = await fetch(`/api/create-file/${currentUser}/${currentProject}/${filename}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ content: '# New Python file\\nprint("Hello, World!")' })
                });

                const result = await response.json();

                if (result.success) {
                    loadFiles();
                    alert('File created successfully!');
                } else {
                    alert('Error creating file');
                }
            } catch (error) {
                alert('Error creating file');
            }
        }

        // File upload functionality
        document.getElementById('fileInput').addEventListener('change', handleFileUpload);

        // Drag and drop functionality
        const uploadArea = document.getElementById('uploadArea');
        
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('dragover');
        });

        uploadArea.addEventListener('dragleave', () => {
            uploadArea.classList.remove('dragover');
        });

        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('dragover');
            handleFileUpload({ target: { files: e.dataTransfer.files } });
        });

        async function handleFileUpload(event) {
            const files = event.target.files;
            
            for (let file of files) {
                const formData = new FormData();
                formData.append('file', file);

                try {
                    const response = await fetch(`/api/upload/${currentUser}/${currentProject}`, {
                        method: 'POST',
                        body: formData
                    });

                    const result = await response.json();

                    if (result.success) {
                        console.log(`${file.name} uploaded successfully`);
                    } else {
                        alert(`Error uploading ${file.name}`);
                    }
                } catch (error) {
                    alert(`Error uploading ${file.name}`);
                }
            }

            loadFiles();
            document.getElementById('fileInput').value = '';
        }

        function showAlert(elementId, message, type) {
            const alertElement = document.getElementById(elementId);
            alertElement.innerHTML = `<div class="alert alert-${type}">${message}</div>`;
            setTimeout(() => {
                alertElement.innerHTML = '';
            }, 5000);
        }

        function formatFileSize(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        // Close modal when clicking outside
        window.onclick = function(event) {
            const modal = document.getElementById('fileModal');
            if (event.target === modal) {
                closeModal();
            }
        }
    </script>
</body>
</html>
    ''')

@file_manager_app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    # Check if credentials match any active session
    for user_id, session_data in file_manager_sessions.items():
        if (session_data['username'] == username and 
            session_data['password'] == password and 
            session_data['expiry'] > datetime.now()):
            
            # Find first project for this user
            project_name = None
            if user_id in user_files and user_files[user_id]:
                project_name = list(user_files[user_id].keys())[0]
            
            session['user_id'] = str(user_id)
            session['username'] = username
            session['project'] = project_name
            
            return jsonify({
                'success': True,
                'user': str(user_id),
                'project': project_name or 'Default'
            })
    
    return jsonify({'success': False, 'message': 'Invalid credentials or session expired'})

@file_manager_app.route('/api/files/<user_id>/<project_name>')
def api_get_files(user_id, project_name):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    # If no project name, create a default project
    if project_name == 'Default' or project_name == 'undefined' or not project_name:
        project_name = 'Main'
        # Create project if it doesn't exist
        if int(user_id) not in user_files:
            user_files[int(user_id)] = {}
        if project_name not in user_files[int(user_id)]:
            user_files[int(user_id)][project_name] = 'main.py'
            save_user_project(int(user_id), 'main.py', project_name)
    
    project_dir = os.path.join(uploaded_files_dir, user_id, project_name)
    
    # Create directory if it doesn't exist
    if not os.path.exists(project_dir):
        os.makedirs(project_dir, exist_ok=True)
        # Create a default main.py file
        default_content = '# Welcome to your Python project!\nprint("Hello World!")\n'
        with open(os.path.join(project_dir, 'main.py'), 'w') as f:
            f.write(default_content)
    
    files = []
    for item in os.listdir(project_dir):
        item_path = os.path.join(project_dir, item)
        files.append({
            'name': item,
            'type': 'directory' if os.path.isdir(item_path) else 'file',
            'size': os.path.getsize(item_path) if os.path.isfile(item_path) else 0,
            'modified': os.path.getmtime(item_path)
        })
    
    return jsonify({'success': True, 'files': files})

@file_manager_app.route('/api/file-content/<user_id>/<project_name>/<filename>')
def api_get_file_content(user_id, project_name, filename):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    file_path = os.path.join(uploaded_files_dir, user_id, project_name, filename)
    
    if not os.path.exists(file_path):
        return jsonify({'success': False, 'message': 'File not found'})
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'success': True, 'content': content})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@file_manager_app.route('/api/save-file/<user_id>/<project_name>/<filename>', methods=['POST'])
def api_save_file(user_id, project_name, filename):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    data = request.get_json()
    content = data.get('content', '')
    
    project_dir = os.path.join(uploaded_files_dir, user_id, project_name)
    os.makedirs(project_dir, exist_ok=True)
    
    file_path = os.path.join(project_dir, filename)
    
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'success': True, 'message': 'File saved successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@file_manager_app.route('/api/create-file/<user_id>/<project_name>/<filename>', methods=['POST'])
def api_create_file(user_id, project_name, filename):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    data = request.get_json()
    content = data.get('content', '')
    
    project_dir = os.path.join(uploaded_files_dir, user_id, project_name)
    os.makedirs(project_dir, exist_ok=True)
    
    file_path = os.path.join(project_dir, filename)
    
    if os.path.exists(file_path):
        return jsonify({'success': False, 'message': 'File already exists'})
    
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'success': True, 'message': 'File created successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@file_manager_app.route('/api/delete-file/<user_id>/<project_name>/<filename>', methods=['DELETE'])
def api_delete_file(user_id, project_name, filename):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    file_path = os.path.join(uploaded_files_dir, user_id, project_name, filename)
    
    if not os.path.exists(file_path):
        return jsonify({'success': False, 'message': 'File not found'})
    
    try:
        if os.path.isdir(file_path):
            shutil.rmtree(file_path)
        else:
            os.remove(file_path)
        return jsonify({'success': True, 'message': 'File deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@file_manager_app.route('/api/upload/<user_id>/<project_name>', methods=['POST'])
def api_upload_file(user_id, project_name):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file provided'})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No selected file'})
    
    project_dir = os.path.join(uploaded_files_dir, user_id, project_name)
    os.makedirs(project_dir, exist_ok=True)
    
    try:
        file.save(os.path.join(project_dir, file.filename))
        return jsonify({'success': True, 'message': 'File uploaded successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@file_manager_app.route('/api/run-file/<user_id>/<project_name>/<filename>', methods=['POST'])
def api_run_file(user_id, project_name, filename):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'})
    
    file_path = os.path.join(uploaded_files_dir, user_id, project_name, filename)
    
    if not os.path.exists(file_path):
        return jsonify({'success': False, 'message': 'File not found'})
    
    if not filename.endswith('.py'):
        return jsonify({'success': False, 'message': 'Only Python files can be executed'})
    
    try:
        # Change to project directory
        original_cwd = os.getcwd()
        project_dir = os.path.join(uploaded_files_dir, user_id, project_name)
        os.chdir(project_dir)
        
        # Auto-install requirements if needed
        try:
            # Read file content to find imports
            with open(filename, 'r') as f:
                content = f.read()
            
            # Extract import statements
            import_lines = re.findall(r'^(?:from\s+(\w+)|import\s+(\w+))', content, re.MULTILINE)
            modules = [line[0] if line[0] else line[1] for line in import_lines]
            
            # Common module mappings
            module_mappings = {
                'cv2': 'opencv-python',
                'PIL': 'Pillow',
                'bs4': 'beautifulsoup4',
                'skimage': 'scikit-image',
                'sklearn': 'scikit-learn',
                'yaml': 'PyYAML',
                'requests': 'requests',
                'numpy': 'numpy',
                'pandas': 'pandas',
                'matplotlib': 'matplotlib',
                'seaborn': 'seaborn',
                'flask': 'Flask',
                'django': 'Django',
                'fastapi': 'fastapi',
                'telebot': 'pyTelegramBotAPI',
                'telegram': 'python-telegram-bot'
            }
            
            # Install missing modules
            for module in modules:
                if module in module_mappings:
                    try:
                        subprocess.run(['pip', 'install', module_mappings[module]], 
                                     capture_output=True, timeout=30, check=False)
                    except:
                        pass
                elif module not in ['os', 'sys', 'time', 'datetime', 'json', 're', 'math', 'random']:
                    try:
                        subprocess.run(['pip', 'install', module], 
                                     capture_output=True, timeout=30, check=False)
                    except:
                        pass
        except:
            pass  # Continue even if requirement installation fails
        
        # Run the Python file
        result = subprocess.run(['python3', filename], 
                              capture_output=True, 
                              text=True, 
                              timeout=60)
        
        os.chdir(original_cwd)
        
        if result.returncode == 0:
            output = result.stdout
            if not output:
                output = "File executed successfully (no output)"
            return jsonify({'success': True, 'output': output})
        else:
            error = result.stderr
            if not error:
                error = "Unknown error occurred"
            return jsonify({'success': False, 'error': error})
    
    except subprocess.TimeoutExpired:
        os.chdir(original_cwd)
        return jsonify({'success': False, 'error': 'Execution timeout (60 seconds)'})
    except Exception as e:
        os.chdir(original_cwd)
        return jsonify({'success': False, 'error': str(e)})

def start_file_manager_server():
    global file_manager_running
    if not file_manager_running:
        def run_server():
            try:
                file_manager_app.run(
                    host='0.0.0.0',
                    port=file_manager_port,
                    debug=False,
                    use_reloader=False,
                    threaded=True
                )
            except Exception as e:
                print(f"File manager server error: {e}")
        
        threading.Thread(target=run_server, daemon=True).start()
        file_manager_running = True
        print(f"File manager server started on port {file_manager_port}")

# Bot Handlers
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if bot_locked and message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⚠️ The bot is currently locked. Please try again later.")
        return

    user_id = message.from_user.id
    user_name = message.from_user.first_name
    username = f"@{message.from_user.username}" if message.from_user.username else "N/A"

    # Add to active users
    if user_id not in active_users:
        active_users.add(user_id)
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
        conn.commit()
        conn.close()

        # Notify admin
        notify_admin = (f"🎉 New user joined!\n\n"
                      f"👤 Name: {user_name}\n"
                      f"🆔 ID: {user_id}\n"
                      f"📌 Username: {username}")
        bot.send_message(ADMIN_ID, notify_admin)

    # Welcome message
    slots_used = get_user_slots(user_id)
    welcome_msg = (f"👋 Welcome, {user_name}!\n\n"
                  f"🆔 Your ID: {user_id}\n"
                  f"📌 Username: {username}\n"
                  f"📊 Project Slots: {slots_used}/{max_slots}\n\n"
                  f"Use the buttons below to manage your projects:")
    
    bot.send_message(message.chat.id, welcome_msg, reply_markup=create_main_menu(user_id))

@bot.callback_query_handler(func=lambda call: call.data == 'my_projects')
def show_projects(call):
    user_id = call.from_user.id
    bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=create_projects_menu(user_id)
    )

@bot.callback_query_handler(func=lambda call: call.data == 'new_project')
def new_project(call):
    user_id = call.from_user.id
    
    if not can_create_project(user_id):
        bot.answer_callback_query(call.id, "⚠️ You've reached your project limit. Please delete some projects or contact the admin.")
        return
    
    msg = bot.send_message(call.message.chat.id, "📝 Please send me your project file (Python .py or .zip):")
    bot.register_next_step_handler(msg, process_new_project, user_id)

def process_new_project(message, user_id):
    if message.content_type != 'document':
        bot.send_message(message.chat.id, "⚠️ Please send a file.")
        return
    
    file_id = message.document.file_id
    file_info = bot.get_file(file_id)
    file_name = message.document.file_name
    
    if not (file_name.endswith('.py') or file_name.endswith('.zip')):
        bot.send_message(message.chat.id, "⚠️ Only Python (.py) files or zip archives are supported.")
        return
    
    # Ask for project name
    msg = bot.send_message(message.chat.id, "📝 Please enter a name for your project:")
    bot.register_next_step_handler(msg, process_project_name, user_id, file_id, file_name)

def process_project_name(message, user_id, file_id, file_name):
    project_name = message.text.strip()
    
    if not project_name:
        bot.send_message(message.chat.id, "⚠️ Project name cannot be empty.")
        return
    
    if user_id in user_files and project_name in user_files[user_id]:
        bot.send_message(message.chat.id, "⚠️ A project with this name already exists.")
        return
    
    # Download and save the file
    try:
        downloaded_file = bot.download_file(bot.get_file(file_id).file_path)
        project_dir = os.path.join(uploaded_files_dir, str(user_id), project_name)
        os.makedirs(project_dir, exist_ok=True)
        
        if file_name.endswith('.zip'):
            with zipfile.ZipFile(downloaded_file, 'r') as zip_ref:
                zip_ref.extractall(project_dir)
        else:
            with open(os.path.join(project_dir, file_name), 'wb') as f:
                f.write(downloaded_file)
        
        # Update user data
        if user_id not in user_files:
            user_files[user_id] = {}
        user_files[user_id][project_name] = file_name
        save_user_project(user_id, file_name, project_name)
        
        # Update slots
        slots_used = get_user_slots(user_id) + 1
        user_slots[user_id] = slots_used
        update_user_slots(user_id, slots_used)
        
        bot.send_message(message.chat.id, f"✅ Project '{project_name}' created successfully!", 
                        reply_markup=create_projects_menu(user_id))
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error creating project: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('project_'))
def show_project(call):
    user_id = call.from_user.id
    project_name = call.data.split('_', 1)[1]
    
    bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=create_project_menu(user_id, project_name)
    )

@bot.callback_query_handler(func=lambda call: call.data == 'file_manager')
def handle_file_manager_cmd(call):
    user_id = call.from_user.id
    
    # Check for existing session
    if user_id in file_manager_sessions and file_manager_sessions[user_id]['expiry'] > datetime.now():
        session = file_manager_sessions[user_id]
        # Generate proper replit URL
        replit_url = file_manager_base_url
        
        message = "📁 File Manager is Ready!\n\n"
        message += f"🌐 URL: {replit_url}\n"
        message += f"👤 Username (click to copy): `{session['username']}`\n"
        message += f"🔑 Password (click to copy): `{session['password']}`\n\n"
        message += f"⚠️ Session will expire at {session['expiry'].strftime('%H:%M:%S')}\n\n"
        message += "💡 Tap on username or password to copy"
        
        markup = types.InlineKeyboardMarkup()
        copy_user_btn = types.InlineKeyboardButton('📋 Copy Username', callback_data=f'copy_username_{user_id}')
        copy_pass_btn = types.InlineKeyboardButton('📋 Copy Password', callback_data=f'copy_password_{user_id}')
        open_btn = types.InlineKeyboardButton('🌐 Open File Manager', url=replit_url)
        stop_btn = types.InlineKeyboardButton('🛑 Stop Session', callback_data='stop_fm_session')
        markup.add(copy_user_btn, copy_pass_btn)
        markup.add(open_btn)
        markup.add(stop_btn)
        
        bot.send_message(call.message.chat.id, message, reply_markup=markup, parse_mode='Markdown')
        return
    
    # Create new session
    username = f"user_{user_id}"
    password = generate_password()
    expiry = datetime.now() + timedelta(minutes=session_timeout)
    
    # Use the configured base URL
    replit_url = file_manager_base_url
    
    session_data = {
        'url': replit_url,
        'username': username,
        'password': password,
        'expiry': expiry
    }
    
    file_manager_sessions[user_id] = session_data
    save_file_manager_session(user_id, session_data)
    
    # Ensure file manager server is running
    start_file_manager_server()
    
    message = "📁 File Manager Session Created!\n\n"
    message += f"🌐 URL: {replit_url}\n"
    message += f"👤 Username (click to copy): `{username}`\n"
    message += f"🔑 Password (click to copy): `{password}`\n\n"
    message += f"⚠️ Session will expire at {expiry.strftime('%H:%M:%S')}\n\n"
    message += "💡 Tap on username or password to copy"
    
    markup = types.InlineKeyboardMarkup()
    copy_user_btn = types.InlineKeyboardButton('📋 Copy Username', callback_data=f'copy_username_{user_id}')
    copy_pass_btn = types.InlineKeyboardButton('📋 Copy Password', callback_data=f'copy_password_{user_id}')
    open_btn = types.InlineKeyboardButton('🌐 Open File Manager', url=replit_url)
    stop_btn = types.InlineKeyboardButton('🛑 Stop Session', callback_data='stop_fm_session')
    markup.add(copy_user_btn, copy_pass_btn)
    markup.add(open_btn)
    markup.add(stop_btn)
    
    bot.send_message(call.message.chat.id, message, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == 'stop_fm_session')
def stop_file_manager_session(call):
    user_id = call.from_user.id
    
    if user_id in file_manager_sessions:
        del file_manager_sessions[user_id]
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute('DELETE FROM file_manager_sessions WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        
        bot.send_message(call.message.chat.id, "🛑 File manager session stopped.")
    else:
        bot.send_message(call.message.chat.id, "⚠️ No active file manager session found.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('copy_username_'))
def copy_username(call):
    user_id = call.from_user.id
    
    if user_id in file_manager_sessions:
        username = file_manager_sessions[user_id]['username']
        bot.answer_callback_query(call.id, f"Username copied: {username}")
        bot.send_message(call.message.chat.id, f"📋 Username: `{username}`", parse_mode='Markdown')
    else:
        bot.answer_callback_query(call.id, "Session not found")

@bot.callback_query_handler(func=lambda call: call.data.startswith('copy_password_'))
def copy_password(call):
    user_id = call.from_user.id
    
    if user_id in file_manager_sessions:
        password = file_manager_sessions[user_id]['password']
        bot.answer_callback_query(call.id, f"Password copied: {password}")
        bot.send_message(call.message.chat.id, f"🔑 Password: `{password}`", parse_mode='Markdown')
    else:
        bot.answer_callback_query(call.id, "Session not found")

# Cleanup function for expired sessions
def cleanup_sessions():
    while True:
        now = datetime.now()
        expired = []
        
        for user_id, session in file_manager_sessions.items():
            if session['expiry'] < now:
                expired.append(user_id)
        
        for user_id in expired:
            del file_manager_sessions[user_id]
            conn = sqlite3.connect('bot_data.db')
            c = conn.cursor()
            c.execute('DELETE FROM file_manager_sessions WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
        
        time.sleep(60)  # Check every minute

# Start background tasks
threading.Thread(target=cleanup_sessions, daemon=True).start()
start_file_manager_server()

@bot.callback_query_handler(func=lambda call: call.data.startswith('manage_'))
def manage_project(call):
    user_id = call.from_user.id
    project_name = call.data.split('_', 1)[1]
    
    if user_id not in user_files or project_name not in user_files[user_id]:
        bot.send_message(call.message.chat.id, "⚠️ Project not found.")
        return
    
    project_dir = os.path.join(uploaded_files_dir, str(user_id), project_name)
    if not os.path.exists(project_dir):
        bot.send_message(call.message.chat.id, "⚠️ Project directory not found.")
        return
    
    files = os.listdir(project_dir)
    file_list = "\n".join([f"📄 {f}" for f in files if os.path.isfile(os.path.join(project_dir, f))])
    
    if not file_list:
        file_list = "No files found"
    
    message = f"📁 Project: {project_name}\n\n{file_list}\n\nUse File Manager for detailed operations."
    markup = types.InlineKeyboardMarkup()
    back_btn = types.InlineKeyboardButton('🔙 Back', callback_data=f'project_{project_name}')
    markup.add(back_btn)
    
    bot.send_message(call.message.chat.id, message, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('deploy_'))
def deploy_project(call):
    user_id = call.from_user.id
    project_name = call.data.split('_', 1)[1]
    
    if user_id not in user_files or project_name not in user_files[user_id]:
        bot.send_message(call.message.chat.id, "⚠️ Project not found.")
        return
    
    project_dir = os.path.join(uploaded_files_dir, str(user_id), project_name)
    main_file = user_files[user_id][project_name]
    main_file_path = os.path.join(project_dir, main_file)
    
    if not os.path.exists(main_file_path):
        bot.send_message(call.message.chat.id, "⚠️ Main file not found.")
        return
    
    try:
        # Auto-install requirements
        with open(main_file_path, 'r') as f:
            content = f.read()
        
        # Extract imports and install packages
        import_lines = re.findall(r'^(?:from\s+(\w+)|import\s+(\w+))', content, re.MULTILINE)
        modules = [line[0] if line[0] else line[1] for line in import_lines]
        
        module_mappings = {
            'telebot': 'pyTelegramBotAPI',
            'requests': 'requests',
            'flask': 'Flask',
            'fastapi': 'fastapi'
        }
        
        for module in modules:
            if module in module_mappings:
                try:
                    subprocess.run(['pip', 'install', module_mappings[module]], 
                                 capture_output=True, timeout=30, check=False)
                except:
                    pass
        
        # Start the bot in background
        process = subprocess.Popen(['python3', main_file_path], 
                                 cwd=project_dir,
                                 stdout=subprocess.PIPE, 
                                 stderr=subprocess.PIPE)
        
        time.sleep(2)  # Give time to start
        
        if process.poll() is None:
            bot.send_message(call.message.chat.id, 
                           f"✅ Project '{project_name}' deployed successfully!\n"
                           f"🚀 Process ID: {process.pid}\n"
                           f"📄 Main File: {main_file}")
        else:
            stderr = process.stderr.read().decode()
            bot.send_message(call.message.chat.id, 
                           f"❌ Deployment failed:\n{stderr}")
    
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Error deploying project: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_'))
def delete_project(call):
    user_id = call.from_user.id
    project_name = call.data.split('_', 1)[1]
    
    if user_id not in user_files or project_name not in user_files[user_id]:
        bot.send_message(call.message.chat.id, "⚠️ Project not found.")
        return
    
    markup = types.InlineKeyboardMarkup()
    confirm_btn = types.InlineKeyboardButton('✅ Yes, Delete', callback_data=f'confirm_delete_{project_name}')
    cancel_btn = types.InlineKeyboardButton('❌ Cancel', callback_data=f'project_{project_name}')
    markup.add(confirm_btn, cancel_btn)
    
    bot.send_message(call.message.chat.id, 
                    f"⚠️ Are you sure you want to delete project '{project_name}'?\n"
                    f"This action cannot be undone!", 
                    reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_delete_'))
def confirm_delete_project(call):
    user_id = call.from_user.id
    project_name = call.data.split('_', 2)[2]
    
    try:
        # Remove from database
        del user_files[user_id][project_name]
        conn = sqlite3.connect('bot_data.db')
        c = conn.cursor()
        c.execute('DELETE FROM user_files WHERE user_id = ? AND project_name = ?', 
                 (user_id, project_name))
        conn.commit()
        conn.close()
        
        # Remove directory
        project_dir = os.path.join(uploaded_files_dir, str(user_id), project_name)
        if os.path.exists(project_dir):
            shutil.rmtree(project_dir)
        
        # Update slots
        slots_used = get_user_slots(user_id) - 1
        user_slots[user_id] = max(0, slots_used)
        update_user_slots(user_id, user_slots[user_id])
        
        bot.send_message(call.message.chat.id, 
                        f"✅ Project '{project_name}' deleted successfully!",
                        reply_markup=create_projects_menu(user_id))
    
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Error deleting project: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == 'back_to_projects')
def back_to_projects(call):
    user_id = call.from_user.id
    bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=create_projects_menu(user_id)
    )

@bot.callback_query_handler(func=lambda call: call.data == 'back_to_main')
def back_to_main(call):
    user_id = call.from_user.id
    bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=create_main_menu(user_id)
    )

# Admin handlers
@bot.callback_query_handler(func=lambda call: call.data == 'upload')
def admin_upload(call):
    if call.from_user.id != ADMIN_ID:
        return
    
    msg = bot.send_message(call.message.chat.id, "📤 Send me a file to upload:")
    bot.register_next_step_handler(msg, handle_admin_upload)

def handle_admin_upload(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    if message.content_type != 'document':
        bot.send_message(message.chat.id, "⚠️ Please send a document.")
        return
    
    try:
        file_id = message.document.file_id
        file_info = bot.get_file(file_id)
        file_name = message.document.file_name
        
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Save to admin uploads
        admin_dir = os.path.join(uploaded_files_dir, 'admin')
        os.makedirs(admin_dir, exist_ok=True)
        
        with open(os.path.join(admin_dir, file_name), 'wb') as f:
            f.write(downloaded_file)
        
        bot.send_message(message.chat.id, f"✅ File '{file_name}' uploaded successfully!")
    
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error uploading file: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == 'stats')
def show_stats(call):
    if call.from_user.id != ADMIN_ID:
        return
    
    total_users = len(active_users)
    total_projects = sum(len(projects) for projects in user_files.values())
    active_sessions = len(file_manager_sessions)
    
    stats_msg = (f"📊 Bot Statistics:\n\n"
                f"👥 Total Users: {total_users}\n"
                f"📁 Total Projects: {total_projects}\n"
                f"🌐 Active File Manager Sessions: {active_sessions}\n"
                f"🔒 Bot Locked: {'Yes' if bot_locked else 'No'}\n"
                f"🆓 Free Mode: {'Yes' if free_mode else 'No'}")
    
    bot.send_message(call.message.chat.id, stats_msg)

@bot.callback_query_handler(func=lambda call: call.data == 'lock_bot')
def lock_bot_handler(call):
    if call.from_user.id != ADMIN_ID:
        return
    
    global bot_locked
    bot_locked = True
    bot.send_message(call.message.chat.id, "🔒 Bot has been locked. Only admin can use it now.")

@bot.callback_query_handler(func=lambda call: call.data == 'unlock_bot')
def unlock_bot_handler(call):
    if call.from_user.id != ADMIN_ID:
        return
    
    global bot_locked
    bot_locked = False
    bot.send_message(call.message.chat.id, "🔓 Bot has been unlocked. All users can use it now.")

@bot.callback_query_handler(func=lambda call: call.data == 'free_mode')
def free_mode_handler(call):
    if call.from_user.id != ADMIN_ID:
        return
    
    global free_mode
    free_mode = not free_mode
    status = "enabled" if free_mode else "disabled"
    bot.send_message(call.message.chat.id, f"🆓 Free mode has been {status}.")

@bot.callback_query_handler(func=lambda call: call.data == 'broadcast')
def broadcast_handler(call):
    if call.from_user.id != ADMIN_ID:
        return
    
    msg = bot.send_message(call.message.chat.id, "📢 Send me the message to broadcast:")
    bot.register_next_step_handler(msg, handle_broadcast)

def handle_broadcast(message):
    if message.from_user.id != ADMIN_ID:
        return
    
    broadcast_text = message.text
    success_count = 0
    
    for user_id in active_users:
        try:
            bot.send_message(user_id, f"📢 Broadcast Message:\n\n{broadcast_text}")
            success_count += 1
        except:
            pass
    
    bot.send_message(message.chat.id, f"✅ Broadcast sent to {success_count} users.")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.from_user.id
    
    if bot_locked and user_id != ADMIN_ID:
        bot.send_message(message.chat.id, "⚠️ The bot is currently locked.")
        return
    
    # Check if user can create projects
    if not can_create_project(user_id):
        bot.send_message(message.chat.id, "⚠️ You've reached your project limit. Please delete some projects first.")
        return
    
    file_name = message.document.file_name
    if not (file_name.endswith('.py') or file_name.endswith('.zip')):
        bot.send_message(message.chat.id, "⚠️ Only Python (.py) files or zip archives are supported.")
        return
    
    # Ask for project name
    msg = bot.send_message(message.chat.id, "📝 Please enter a name for your project:")
    bot.register_next_step_handler(msg, process_project_name, user_id, message.document.file_id, file_name)

if __name__ == '__main__':
    print("Bot starting...")
    bot.infinity_polling()