from flask import Flask, render_template, request, jsonify
import os
import pgpy
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

app = Flask(__name__)

sender_email = os.environ['EMAIL']
sender_password = os.environ['NOTIFY_PASSWORD']
smtp_server = os.environ['NOTIFY_SMTP_SERVER']
smtp_port = int(os.environ['NOTIFY_SMTP_PORT'])

def encrypt_message(message, public_key_path):
    with open(public_key_path, 'r') as key_file:
        key_data = key_file.read()
    public_key, _ = pgpy.PGPKey.from_blob(key_data)  # Extract the key from the tuple
    encrypted_message = str(public_key.encrypt(pgpy.PGPMessage.new(message)))
    return encrypted_message

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/save_message', methods=['POST'])
def save_message():
    message = request.form['message']
    encrypted_message = encrypt_message(message, 'public_key.asc')
    with open('messages.txt', 'a') as f:
        f.write(encrypted_message + '\n\n')
    send_email_notification(encrypted_message)
    return jsonify({'success': True})

def send_email_notification(message):
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = sender_email
    msg['Subject'] = "New Hush Line Message Received"

    body = f"{message}"
    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, sender_email, msg.as_string())
    except Exception as e:
        print(f"Error sending email notification: {e}")

@app.route('/pgp_owner_info')
def pgp_owner_info():
    with open('public_key.asc', 'r') as key_file:
        key_data = key_file.read()
    public_key, _ = pgpy.PGPKey.from_blob(key_data)
    owner = f"Owner: {public_key.userids[0].name} <{public_key.userids[0].email}>"
    key_id = f"Key ID: {public_key.keyid}"
    created = f"Created: {public_key.created}"
    expires = f"Expires: {public_key.expires_at}"
    response_data = {'owner_info': owner, 'key_id': key_id, 'created': created, 'expires': expires}
    print("Sending JSON object:", response_data)  # Add this line to log the JSON object
    return jsonify(response_data)

if __name__ == '__main__':
    app.run(debug=True)
