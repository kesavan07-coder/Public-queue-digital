from flask import Flask, render_template, request, jsonify, url_for
from flask_socketio import SocketIO, emit
import socket
import qrcode
import os
import database

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
# Allow CORS for WebSockets during demo
socketio = SocketIO(app, cors_allowed_origins="*")

# Helper to get local IP
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

with app.app_context():
    if not os.path.exists('static/qr'):
        os.makedirs('static/qr')
    database.init_db()

@app.route('/')
def index():
    # Dynamically get the base URL (works for localhost or public internet)
    base_url = request.host_url.rstrip('/')
    url = f"{base_url}/user"
    
    # Also get IP for local logging if needed
    ip = get_local_ip()
    
    # Generate QR Code image
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    qr_path = os.path.join('static', 'qr', 'token_qr.png')
    img.save(qr_path)
    
    return render_template('laptop.html', ip=ip, url=url)

@app.route('/user')
def user_view():
    return render_template('user.html')

@app.route('/display')
def display_view():
    return render_template('display.html')

@app.route('/counter')
def counter_view():
    return render_template('counter.html')

@app.route('/kiosk')
def kiosk_view():
    return render_template('kiosk.html')

# API Endpoints
@app.route('/api/generate', methods=['POST'])
def generate():
    data = request.json or {}
    category = data.get('category', 'OPD')
    token = database.generate_token(category)
    # Notify everyone that queue updated
    socketio.emit('queue_update', get_full_queue_state())
    return jsonify(token)

@app.route('/api/call_next', methods=['POST'])
def call_next():
    data = request.json
    counter_num = data.get('counter_number', 1)
    token = database.call_next(counter_num)
    
    if token:
        # Broadcast the called token to all
        socketio.emit('token_called', token)
        socketio.emit('queue_update', get_full_queue_state())
        return jsonify({'success': True, 'token': token})
    return jsonify({'success': False, 'message': 'No waiting tokens'})

@app.route('/api/no_show', methods=['POST'])
def no_show():
    data = request.json
    counter_num = data.get('counter_number', 1)
    database.mark_no_show(counter_num)
    socketio.emit('queue_update', get_full_queue_state())
    return jsonify({'success': True})

@app.route('/dashboard')
def dashboard_view():
    stats = database.get_analytics_summary()
    return render_template('dashboard.html', stats=stats)

@app.route('/api/queue_state', methods=['GET'])
def queue_state():
    return jsonify(get_full_queue_state())

@app.route('/api/average_service_time', methods=['GET'])
def average_service_time():
    avg_time = database.get_average_service_time()
    return jsonify({'average_service_time': avg_time})

@app.route('/api/counter_stats', methods=['GET'])
def counter_stats():
    return jsonify({
        'counters': database.get_counter_stats(),
        'recommended': database.get_recommended_counter()
    })

def get_full_queue_state():
    return {
        'waiting': database.get_waiting_tokens(),
        'serving': database.get_serving_tokens()
    }

@socketio.on('connect')
def test_connect():
    emit('queue_update', get_full_queue_state())

if __name__ == '__main__':
    print("Pre-start checkout...")
    # Port for Render (default to 5001 for local)
    port = int(os.environ.get("PORT", 5001))
    ip = get_local_ip()
    print(f"==================================================")
    print(f"SERVER STARTING")
    print(f"Local: http://localhost:{port}")
    print(f"Network: http://{ip}:{port}")
    print(f"==================================================")
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
