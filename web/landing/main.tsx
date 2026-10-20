import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '../src/index.css'
import '../src/components.css'
import { Landing } from '../src/pages/Landing'

// Static public page: no router, no API. The app itself runs only on the person's own machine.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Landing staticSite />
  </StrictMode>,
)
