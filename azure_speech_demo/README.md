# Voice Helpdesk Agent (Azure + Streaming)

## Features
- Voice-to-voice conversation
- Azure Speech (STT + TTS)
- Azure OpenAI (streaming)
- Azure AI Search (RAG)
- ServiceNow ticket creation
- WebSocket low-latency streaming

## Setup

### 1. Setup env and install dependencies
conda create -n azure-speech-demo python=3.11 -y
conda activate azure-speech-demo
pip install fastapi uvicorn[standard] httpx langchain-openai langgraph langchain-core


### 3. Run app
uvicorn app:app --reload

## Notes
- Replace Speech SDK key in index.html
