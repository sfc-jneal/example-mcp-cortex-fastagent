import logo from './logo.svg';
import './App.css';
import { useState } from "react";

const BACKEND_URL = process.env.REACT_APP_API_URL || "http://localhost:8000";

function App() {
  const [response, setResponse] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);

  // For local testing, you can set a sample session cookie in your browser's dev tools:
  // document.cookie = "session=sample.jwt.token; path=/";
    
  const handleStream = async (query) => {
    setResponse("");
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
                break;
              }
              fullText += data;
              setResponse(fullText);
            }
          }
        }
      }
    } catch (err) {
      setResponse(prev => prev + "\n[Error streaming response]");
    } finally {
      setIsStreaming(false);
    }
  };

  return (
    <div>
      <h2>Ask a Question</h2>
      <input
        type="text"
        placeholder="Enter your query"
        onKeyDown={(e) => {
          if (e.key === "Enter") handleStream(e.target.value);
        }}
      />
      {response && (<p>Response: </p>)}
      <pre>{response}</pre>
      {isStreaming && <p>Streaming response...</p>}
    </div>
  );
}

export default App;
