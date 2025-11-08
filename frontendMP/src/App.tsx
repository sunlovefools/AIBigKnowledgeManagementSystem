
import React from 'react';
import './App.css';


const App: React.FC = () => {

  // Requirement 3: Logout Function
  const handleLogout = (): void => {
    console.log('Logging out...');

    localStorage.removeItem('authToken');

    alert('You have been logged out!');
  
    window.location.href = '/login.html';
  };

  return (
    <div className="container">
      
      <header className="header">
        <h1>AI Big Knowledge Management</h1>
        
        {/* Requirement 3: Logout Button 
        */}
        <button id="logoutButton" onClick={handleLogout}>
          Logout
        </button>
      </header>

      <main className="main-content">
        
        <section className="chat-section">
          
          {/* Requirement 1: Model Interaction */}
          <h3>Model Interaction</h3>
          <div id="model-interaction">
            <p>AI responses will appear here...</p>
          </div>

          {/* Requirement 4: Query Placeholder */}
          <div id="query-placeholder">
            <input type="text" id="query-input" placeholder="Type your question here..." />
            <button>Send</button>
          </div>

        </section>

        <aside className="sidebar-section">
          
          {/* Requirement 5: File Upload Placeholder */}
          <div className="sidebar-module" id="file-upload-placeholder">
            <h3>Upload Documents</h3>
            <input type="file" />
            <button style={{ marginTop: '10px', width: '100%' }}>Upload</button>
          </div>

          {/* Requirement 2: Data Display Placeholder */}
          <div className="sidebar-module" id="data-display-placeholder">
            <h3>Stored Documents</h3>
            <ul>
              <li>Placeholder_File_1.pdf</li>
              <li>Placeholder_File_2.docx</li>
              <li>Placeholder_File_3.txt</li>
            </ul>
          </div>

        </aside>

      </main>
    </div>
  );
}

export default App;