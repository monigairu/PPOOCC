import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import App from './App.jsx'
import ReviewPage from './pages/ReviewPage.jsx'
import InquiryPage from './pages/InquiryPage.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />} />
        <Route path="/review" element={<ReviewPage />} />
        <Route path="/inquiry" element={<InquiryPage />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
