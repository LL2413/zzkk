import React from 'react'
import { motion } from 'framer-motion'
import './TabBar.css'

const tabs = [
  { id: 'home', label: '首页', icon: '🏠' },
  { id: 'add', label: '添加', icon: '➕', isMain: true },
  { id: 'history', label: '记录', icon: '📋' },
]

function TabBar({ currentTab, onTabChange }) {
  return (
    <nav className="tab-bar">
      <div className="tab-bar-inner">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`tab-item ${currentTab === tab.id ? 'active' : ''} ${tab.isMain ? 'main-tab' : ''}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.isMain ? (
              <motion.div
                className="main-tab-button"
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
              >
                <span className="tab-icon">{tab.icon}</span>
              </motion.div>
            ) : (
              <>
                <span className="tab-icon">{tab.icon}</span>
                <span className="tab-label">{tab.label}</span>
                {currentTab === tab.id && (
                  <motion.div
                    className="tab-indicator"
                    layoutId="tabIndicator"
                    transition={{ type: 'spring', stiffness: 500, damping: 30 }}
                  />
                )}
              </>
            )}
          </button>
        ))}
      </div>
    </nav>
  )
}

export default TabBar
