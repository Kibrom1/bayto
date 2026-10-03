import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { TaskBoard } from './pages/TaskBoard'
import { SessionSetup } from './pages/SessionSetup'
import { BaytoRoom } from './pages/BaytoRoom'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<TaskBoard />} />
        <Route path="/setup/:taskId" element={<SessionSetup />} />
        <Route path="/room/:sessionId" element={<BaytoRoom />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
