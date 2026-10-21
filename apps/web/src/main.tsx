import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import './index.css';
import { IS_STATIC } from './lib/api';
import { Editor } from './routes/Editor';
import { Landing } from './routes/Landing';
import { Viewer } from './routes/Viewer';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/p/:projectId" element={<Viewer />} />
        {/* The editor saves through the API, which a static site does not have. */}
        {!IS_STATIC && <Route path="/edit/:projectId" element={<Editor />} />}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
);
