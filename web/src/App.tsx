import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { SessionSetup } from './pages/SessionSetup'
import { TaskBoard } from './pages/TaskBoard'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<TaskBoard />} />
        <Route path="/tasks/:taskId/session-setup" element={<SessionSetup />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
