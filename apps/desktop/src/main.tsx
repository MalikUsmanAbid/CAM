import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import DesktopWelcome from './DesktopWelcome'

createRoot(document.getElementById('root')!).render(<StrictMode><DesktopWelcome><App /></DesktopWelcome></StrictMode>)
