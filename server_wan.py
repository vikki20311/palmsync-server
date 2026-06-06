from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import uuid
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1GB

# In-memory storage
peers = {}  # device_id -> {'ip': request.remote_addr, 'last_seen': datetime}
transfers = {}  # transfer_id -> {'filename': str, 'size': int, 'sender': str, 'receiver': str,
                #              'chunks': {index: bytes}, 'status': 'pending'|'accepted'|'rejected'|'completed',
                #              'created_at': datetime, 'total_chunks': int, 'chunk_size': int}
# For demo, we store chunks in memory. For larger files, use temp files.

@app.route('/')
def home():
    return "Palmsync WAN Relay Server is running."

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    device_id = data.get('device_id')
    if not device_id:
        return jsonify({'error': 'device_id required'}), 400
    peers[device_id] = {
        'ip': request.remote_addr,
        'last_seen': datetime.utcnow()
    }
    return jsonify({'status': 'registered'})

@app.route('/api/peers', methods=['GET'])
def list_peers():
    # Only return peers active in last 30 seconds
    cutoff = datetime.utcnow() - timedelta(seconds=30)
    active_peers = [
        {'device_id': pid, 'ip': info['ip']}
        for pid, info in peers.items()
        if info['last_seen'] > cutoff
    ]
    return jsonify(active_peers)

@app.route('/api/keepalive', methods=['POST'])
def keepalive():
    data = request.json
    device_id = data.get('device_id')
    if device_id in peers:
        peers[device_id]['last_seen'] = datetime.utcnow()
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'not registered'}), 404

@app.route('/api/transfer/initiate', methods=['POST'])
def initiate_transfer():
    data = request.json
    filename = data.get('filename')
    size = data.get('size')
    sender = data.get('sender')
    receiver = data.get('receiver')
    
    if not all([filename, size, sender, receiver]):
        return jsonify({'error': 'missing fields'}), 400
    
    transfer_id = str(uuid.uuid4())
    # Use 1MB chunks for demo
    chunk_size = 1024 * 1024
    total_chunks = (size + chunk_size - 1) // chunk_size
    
    transfers[transfer_id] = {
        'filename': secure_filename(filename),
        'size': size,
        'sender': sender,
        'receiver': receiver,
        'chunks': {},
        'status': 'pending',
        'created_at': datetime.utcnow(),
        'total_chunks': total_chunks,
        'chunk_size': chunk_size
    }
    return jsonify({'transfer_id': transfer_id})

@app.route('/api/transfer/upload_chunk', methods=['POST'])
def upload_chunk():
    transfer_id = request.form.get('transfer_id')
    index = int(request.form.get('index'))
    file_data = request.files.get('chunk')
    
    if not all([transfer_id, index, file_data]):
        return jsonify({'error': 'missing fields'}), 400
    
    if transfer_id not in transfers:
        return jsonify({'error': 'transfer not found'}), 404
    
    transfers[transfer_id]['chunks'][index] = file_data.read()
    return jsonify({'status': 'ok'})

@app.route('/api/transfer/download_chunk', methods=['GET'])
def download_chunk():
    transfer_id = request.args.get('transfer_id')
    index = int(request.args.get('index'))
    
    if transfer_id not in transfers:
        return jsonify({'error': 'transfer not found'}), 404
    
    chunk_data = transfers[transfer_id]['chunks'].get(index)
    if chunk_data is None:
        return jsonify({'error': 'chunk not found'}), 404
    
    return chunk_data, 200, {'Content-Type': 'application/octet-stream'}

@app.route('/api/transfer/status', methods=['GET'])
def transfer_status():
    transfer_id = request.args.get('transfer_id')
    if transfer_id not in transfers:
        return jsonify({'error': 'not found'}), 404
    t = transfers[transfer_id]
    return jsonify({
        'status': t['status'],
        'filename': t['filename'],
        'size': t['size'],
        'sender': t['sender'],
        'receiver': t['receiver'],
        'total_chunks': t['total_chunks'],
        'received_chunks': len(t['chunks'])
    })

@app.route('/api/transfer/accept', methods=['POST'])
def accept_transfer():
    data = request.json
    transfer_id = data.get('transfer_id')
    if transfer_id not in transfers:
        return jsonify({'error': 'not found'}), 404
    transfers[transfer_id]['status'] = 'accepted'
    return jsonify({'status': 'accepted'})

@app.route('/api/transfer/reject', methods=['POST'])
def reject_transfer():
    data = request.json
    transfer_id = data.get('transfer_id')
    if transfer_id not in transfers:
        return jsonify({'error': 'not found'}), 404
    transfers[transfer_id]['status'] = 'rejected'
    return jsonify({'status': 'rejected'})

@app.route('/api/transfer/complete', methods=['POST'])
def complete_transfer():
    data = request.json
    transfer_id = data.get('transfer_id')
    if transfer_id not in transfers:
        return jsonify({'error': 'not found'}), 404
    transfers[transfer_id]['status'] = 'completed'
    # Clean up chunks to free memory (for demo)
    transfers[transfer_id]['chunks'].clear()
    return jsonify({'status': 'completed'})

@app.route('/api/transfer/incoming', methods=['GET'])
def incoming_transfers():
    device_id = request.args.get('device_id')
    if not device_id:
        return jsonify({'error': 'device_id required'}), 400
    
    incoming = []
    for tid, t in transfers.items():
        if t['receiver'] == device_id and t['status'] == 'pending':
            incoming.append({
                'transfer_id': tid,
                'filename': t['filename'],
                'size': t['size'],
                'sender': t['sender'],
                'created_at': t['created_at'].isoformat()
            })
    return jsonify(incoming)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)