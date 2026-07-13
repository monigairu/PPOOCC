import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import App from './App.jsx'
import ReviewPage from './pages/ReviewPage.jsx'
import InquiryPage from './pages/inquiry/InquiryPage.jsx'
import InquiryListPage from './pages/inquiry/InquiryListPage.jsx'
import InquiryDetailPage from './pages/inquiry/InquiryDetailPage.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />} />
        <Route path="/review" element={<ReviewPage />} />
        <Route path="/inquiry" element={<InquiryPage />} />
        <Route path="/inquiry/tickets" element={<InquiryListPage />} />
        <Route path="/inquiry/tickets/:inquiryId" element={<InquiryDetailPage />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
