import React, { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import HomePage from './pages/HomePage'
import AddPage from './pages/AddPage'
import HistoryPage from './pages/HistoryPage'
import TabBar from './components/TabBar'
import useStore from './store/useStore'
import './styles/app.css'

function App() {
  const [currentTab, setCurrentTab] = useState('home')
  const requestNotificationPermission = useStore(state => state.requestNotificationPermission)
  
  useEffect(() => {
    // 请求通知权限
    requestNotificationPermission()
  }, [])

  const renderPage = () => {
    switch (currentTab) {
      case 'home':
        return <HomePage />
      case 'add':
        return <AddPage onComplete={() => setCurrentTab('home')} />
      case 'history':
        return <HistoryPage />
      default:
        return <HomePage />
    }
  }

  return (
    <div className="app">
      <main className="main-content">
        <AnimatePresence mode="wait">
          <motion.div
            key={currentTab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
            style={{ height: '100%' }}
          >
            {renderPage()}
          </motion.div>
        </AnimatePresence>
      </main>
      <TabBar currentTab={currentTab} onTabChange={setCurrentTab} />
    </div>
  )
}

export default App
