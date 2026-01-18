import React from 'react'
import { motion } from 'framer-motion'
import useStore from '../store/useStore'
import BorrowCard from '../components/BorrowCard'
import './HomePage.css'

function HomePage() {
  const items = useStore(state => state.items)
  const activeItems = items.filter(item => item.status === 'active')
  
  const urgentItems = activeItems.filter(item => {
    if (!item.reminderTime) return false
    const timeLeft = new Date(item.reminderTime).getTime() - Date.now()
    return timeLeft < 30 * 60 * 1000 && timeLeft > 0 // 30分钟内
  })
  
  const normalItems = activeItems.filter(item => !urgentItems.includes(item))

  return (
    <div className="page home-page">
      <header className="home-header">
        <motion.div 
          className="logo-section"
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
        >
          <div className="logo">
            <span className="logo-icon">⏰</span>
          </div>
          <div className="logo-text">
            <h1 className="app-name">还了吗</h1>
            <p className="app-slogan">再也不会忘记归还</p>
          </div>
        </motion.div>
        
        <motion.div 
          className="stats-card"
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.2, duration: 0.4 }}
        >
          <div className="stat-item">
            <span className="stat-number">{activeItems.length}</span>
            <span className="stat-label">待归还</span>
          </div>
          <div className="stat-divider"></div>
          <div className="stat-item">
            <span className="stat-number">{urgentItems.length}</span>
            <span className="stat-label">即将到期</span>
          </div>
          <div className="stat-divider"></div>
          <div className="stat-item">
            <span className="stat-number">{items.filter(i => i.status === 'returned').length}</span>
            <span className="stat-label">已归还</span>
          </div>
        </motion.div>
      </header>

      <section className="items-section">
        {urgentItems.length > 0 && (
          <div className="items-group urgent-group">
            <h2 className="group-title">
              <span className="title-icon">🔥</span>
              即将到期
            </h2>
            <div className="items-list">
              {urgentItems.map((item, index) => (
                <motion.div
                  key={item.id}
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: index * 0.1 }}
                >
                  <BorrowCard item={item} urgent />
                </motion.div>
              ))}
            </div>
          </div>
        )}

        {normalItems.length > 0 && (
          <div className="items-group">
            <h2 className="group-title">
              <span className="title-icon">📦</span>
              正在借用
            </h2>
            <div className="items-list">
              {normalItems.map((item, index) => (
                <motion.div
                  key={item.id}
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: index * 0.1 + 0.2 }}
                >
                  <BorrowCard item={item} />
                </motion.div>
              ))}
            </div>
          </div>
        )}

        {activeItems.length === 0 && (
          <motion.div 
            className="empty-state"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
          >
            <div className="empty-icon animate-float">🎉</div>
            <h3 className="empty-title">太棒了！</h3>
            <p className="empty-description">
              你没有待归还的物品，继续保持这个好习惯！
            </p>
          </motion.div>
        )}
      </section>
    </div>
  )
}

export default HomePage
