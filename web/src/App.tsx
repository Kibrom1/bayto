import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { TaskBoard } from './pages/TaskBoard'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<TaskBoard />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
