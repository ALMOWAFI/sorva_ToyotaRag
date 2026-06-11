# Raspberry Pi 5 Setup — One Time

## 1. Clone and install

```bash
git clone https://github.com/ALMOWAFI/sorva_ToyotaRag.git
cd sorva_ToyotaRag
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
sudo apt install portaudio19-dev -y   # required for pyaudio
pip install pyaudio
```

## 2. Pull Ollama model

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:1.5b
```

## 3. Build the knowledge base index

```bash
python build_index.py
```

## 4. Auto-start the server on boot

```bash
sudo cp autostart/sequoia-rag.service /etc/systemd/system/
sudo systemctl enable sequoia-rag
sudo systemctl start sequoia-rag
```

## 5. Auto-open browser in kiosk mode on boot

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/kiosk.desktop << EOF
[Desktop Entry]
Type=Application
Name=Sequoia Kiosk
Exec=/home/pi/sorva_ToyotaRag/autostart/kiosk.sh
EOF
chmod +x autostart/kiosk.sh
```

## 6. Reboot

```bash
sudo reboot
```

On boot the Pi will:
1. Start Ollama + the RAG server automatically
2. Open Chromium fullscreen showing the assistant UI
3. Start listening for "Jarvis" via the microphone

## Wake word

Say **"Hey Jarvis"** — the orb turns blue, you speak, it answers out loud.
No touching the screen required.

## Microphone

Plug in a USB microphone. Check it's detected:
```bash
arecord -l
```
