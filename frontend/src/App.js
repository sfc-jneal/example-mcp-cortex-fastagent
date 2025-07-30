import './App.css';
import { useState, useRef, useEffect } from "react";

const BACKEND_URL = process.env.REACT_APP_API_URL || "http://localhost:8000";

function App() {
  const [messages, setMessages] = useState([]);
  const [currentQuery, setCurrentQuery] = useState("");
  const [response, setResponse] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingStatus, setStreamingStatus] = useState("");
  const inputRef = useRef(null);
  const chatContainerRef = useRef(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
    }
  }, [messages, response, streamingStatus]);

  // For local testing, you can set a sample session cookie in your browser's dev tools:
  // document.cookie = "session=sample.jwt.token; path=/";
    
  const handleStream = async (query) => {
    if (!query.trim()) return;
    
    // Add user message to chat
    const userMessage = { type: 'user', content: query };
    setMessages(prev => [...prev, userMessage]);
    setCurrentQuery("");
    
    setResponse("");
    setStreamingStatus("");
    setIsStreaming(true);

    try {
      // Call the backend API (main.py) using the environment variable
      const res = await fetch(`${BACKEND_URL}/stream?query=${encodeURIComponent(query)}`, {
        credentials: 'include', // This will send cookies such as 'session' to the backend
      });
      if (!res.body) throw new Error("No response body");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let done = false;
      let fullText = "";
      let isReceivingActualResponse = false;
      
      while (!done) {
        const { value, done: doneReading } = await reader.read();
        done = doneReading;
        if (value) {
          const chunk = decoder.decode(value, { stream: true });
          const events = chunk.split("\n\n");
          for (let event of events) {
            event = event.trim();
            if (!event) continue;
            // Only process lines that start with "data:"
            if (event.startsWith("data:")) {
              let data = event.slice(5).trim();
              if (data === "[DONE]") {
                done = true; // end the stream
                setStreamingStatus("");
                // Add final assistant message to chat
                setMessages(prev => [...prev, { type: 'assistant', content: fullText }]);
                setResponse("");
                break;
              }
              if (data === "[CANCELLED]") {
                setStreamingStatus("Request was cancelled");
                break;
              }
              if (data.startsWith("[ERROR:")) {
                setStreamingStatus(data);
                break;
              }
              
              // Check if this is a status/progress message (contains emojis or is short)
              const isStatusMessage = data.match(/[🤔🔍🧠🔧📊✨⏳]/) || 
                                     data.includes("Processing") || 
                                     data.includes("Analyzing") || 
                                     data.includes("Thinking") || 
                                     data.includes("Preparing") || 
                                     data.includes("Searching") || 
                                     data.includes("Generating") ||
                                     data.includes("working");
              
              if (isStatusMessage && !isReceivingActualResponse) {
                setStreamingStatus(data);
              } else {
                // Once we start getting actual content, mark it and stop showing status
                if (data.length > 0 && !isStatusMessage) {
                  isReceivingActualResponse = true;
                  setStreamingStatus("✍️ Streaming response...");
                }
                
                // Add space between words if needed when concatenating
                fullText += ' ' + data;
                setResponse(fullText);
              }
            }
          }
        }
      }
    } catch (err) {
      setResponse(prev => prev + "\n[Error streaming response]");
      setStreamingStatus("Error occurred");
    } finally {
      setIsStreaming(false);
      setStreamingStatus("");
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (currentQuery.trim() && !isStreaming) {
      handleStream(currentQuery);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <div className="App">
      <div className="header">
        <h1>MCP Analytics Assistant</h1>
        <p>Ask questions about your Snowflake data</p>
      </div>

      <div className="chat-container" ref={chatContainerRef}>
        {messages.length === 0 && !isStreaming && (
          <div className="empty-state">
            <div className="empty-state-icon">💬</div>
            <h3>Welcome to your Analytics Assistant</h3>
            <p>Ask me anything about your Snowflake data. I can help you analyze revenue, trends, and more!</p>
          </div>
        )}

        {messages.map((message, index) => (
          <div key={index} className={`message ${message.type}`}>
            <div className="message-content">
              <div className="response-content">{message.content}</div>
            </div>
          </div>
        ))}

        {isStreaming && streamingStatus && (
          <div className="status-message">
            <div className="typing-indicator">
              <div className="typing-dots">
                <div className="typing-dot"></div>
                <div className="typing-dot"></div>
                <div className="typing-dot"></div>
              </div>
            </div>
            {streamingStatus}
          </div>
        )}

        {isStreaming && response && (
          <div className="message assistant">
            <div className="message-content">
              <div className="response-content">{response}</div>
            </div>
          </div>
        )}
      </div>

      <div className="input-container">
        <form onSubmit={handleSubmit}>
          <div className="input-wrapper">
            <textarea
              ref={inputRef}
              className="message-input"
              placeholder="Ask about your data..."
              value={currentQuery}
              onChange={(e) => setCurrentQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isStreaming}
              rows={1}
            />
            <button
              type="submit"
              className="send-button"
              disabled={!currentQuery.trim() || isStreaming}
            >
              {isStreaming ? (
                <div className="typing-dots">
                  <div className="typing-dot"></div>
                  <div className="typing-dot"></div>
                  <div className="typing-dot"></div>
                </div>
              ) : (
                "→"
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default App;
