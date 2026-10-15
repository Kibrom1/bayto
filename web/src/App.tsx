import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { TaskBoard } from './pages/TaskBoard'
import { SessionSetup } from './pages/SessionSetup'
import { BaytoRoom } from './pages/BaytoRoom'
import { Services } from './pages/Services'
import { Landing } from './pages/Landing'
import { NotFound } from './pages/NotFound'
import './components.css'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/welcome" element={<Landing />} />
        <Route element={<AppShell />}>
          <Route path="/" element={<TaskBoard />} />
          <Route path="/setup/:taskId" element={<SessionSetup />} />
          <Route path="/room/:sessionId" element={<BaytoRoom />} />
          <Route path="/services" element={<Services />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
