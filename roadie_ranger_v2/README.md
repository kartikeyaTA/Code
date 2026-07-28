## README: VoiceLive Assistant

This application is a **Real-Time Voice AI Assistant** built with **FastAPI** and **Azure AI VoiceLive**. It enables a seamless, low-latency voice conversation between a web browser and an Azure-hosted AI agent. The app handles full-duplex audio streaming, speech-to-text transcription, and persistent session history.

---

## 🚀 What This App Does

* **Real-Time Voice Interaction:** Streams audio from your microphone to Azure AI and plays back synthesized AI responses with minimal latency.
* **Live Transcription:** Provides real-time visual feedback by displaying "deltas" (partial text) for both the user's speech and the agent's response.
* **Proactive Greeting:** The AI automatically initiates the conversation with a welcome message upon connection.
* **Interim Responses:** Includes "filler" logic (e.g., *"Just a moment please..."*) to keep the user engaged during complex tool calls or processing.
* **Session Management:**
    * Automatically saves conversation history to local JSON files.
    * Features a REST API to list past sessions and reload specific chat histories.
* **Intelligent Interruption:** Automatically cancels the AI's current speech if it detects the user has started speaking again ("Barge-in" support).

---

## 🛠️ Setup Steps

### 1. Prerequisites
* **Python 3.9+**
* **Azure Subscription** with access to Azure AI Foundry / VoiceLive resources.
* **Azure CLI** installed and logged in (`az login`).

### 2. Environment Configuration
Create a `.env` file in the root directory and populate it with your Azure credentials:

```env
AZURE_VOICELIVE_ENDPOINT=your_endpoint_url
AZURE_VOICELIVE_AGENT_ID=your_agent_id
AZURE_VOICELIVE_PROJECT_NAME=your_project_name
AZURE_STORAGE_ACCOUNT_URL=
#user web usage limit details
USER_DAILY_SESSION_LIMIT=10
USER_DAILY_MINUTES_LIMIT=60
```

### 3. Installation
Install the required Python packages:

```bash
pip install -r requirements.txt
```

### 4. Running the Application
Start the server using Uvicorn:

```bash
uvicorn main:app --reload
```
The application will be available at `http://127.0.0.1:8000`.

---

## 📝 Summary of Architecture

| Component | Responsibility |
| :--- | :--- |
| **FastAPI** | Serves the frontend (`index.html`) and manages WebSocket/REST endpoints. |
| **WebSocket** | Acts as the bridge, relaying raw PCM16 audio chunks between the browser and the server. |
| **VoiceLiveSession** | A state machine class that manages the `azure-ai-voicelive` connection and handles asynchronous events. |
| **Azure CLI Credential** | Used for seamless authentication with Azure services without hardcoding keys. |
| **JSON Persistence** | Stores session data in the `/sessions` folder for long-term retrieval. |

### How the Data Flows
1.  **User Speaks:** Browser captures audio $\rightarrow$ WebSocket $\rightarrow$ Server $\rightarrow$ Azure VoiceLive.
2.  **AI Responds:** Azure $\rightarrow$ Server $\rightarrow$ WebSocket $\rightarrow$ Browser (Audio Playback + Text Display).
3.  **Persistence:** Every finalized "turn" is appended to a local `.json` file identified by a unique `UUID`.