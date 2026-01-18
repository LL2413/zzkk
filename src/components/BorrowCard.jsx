import React from 'react'
import { motion } from 'framer-motion'
import { formatDistanceToNow, format, isPast } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import useStore, { BORROW_TYPES } from '../store/useStore'
import './BorrowCard.css'

function BorrowCard({ item, urgent = false, showActions = true }) {
  const returnItem = useStore(state => state.returnItem)
  const deleteItem = useStore(state => state.deleteItem)
  
  const type = BORROW_TYPES.find(t => t.id === item.type) || BORROW_TYPES.find(t => t.id === 'other')
  
  const getTimeStatus = () => {
    if (!item.reminderTime) return null
    
    const reminderDate = new Date(item.reminderTime)
    const isOverdue = isPast(reminderDate)
    
    if (isOverdue) {
      return {
        text: '已超时',
        className: 'overdue'
      }
    }
    
    const timeLeft = formatDistanceToNow(reminderDate, { locale: zhCN, addSuffix: false })
    return {
      text: `${timeLeft}后到期`,
      className: urgent ? 'urgent' : 'normal'
    }
  }
  
  const timeStatus = getTimeStatus()
  
  const handleReturn = (e) => {
    e.stopPropagation()
    returnItem(item.id)
  }
  
  const handleDelete = (e) => {
    e.stopPropagation()
    if (window.confirm('确定要删除这条记录吗？')) {
      deleteItem(item.id)
    }
  }

  return (
    <motion.div 
      className={`borrow-card ${urgent ? 'urgent' : ''} ${item.status === 'returned' ? 'returned' : ''}`}
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
      layout
    >
      <div className="card-icon">
        <span>{type.icon}</span>
      </div>
      
      <div className="card-content">
        <div className="card-header">
          <h3 className="card-title">{type.name}</h3>
          {timeStatus && item.status === 'active' && (
            <span className={`time-badge ${timeStatus.className}`}>
              {timeStatus.text}
            </span>
          )}
          {item.status === 'returned' && (
            <span className="time-badge returned">已归还</span>
          )}
        </div>
        
        {item.note && (
          <p className="card-note">{item.note}</p>
        )}
        
        <div className="card-meta">
          <span className="meta-item">
            📍 {item.location || '未知地点'}
          </span>
          <span className="meta-item">
            🕐 {format(new Date(item.createdAt), 'MM/dd HH:mm')}
          </span>
        </div>
      </div>
      
      {showActions && item.status === 'active' && (
        <div className="card-actions">
          <motion.button
            className="action-btn return-btn"
            onClick={handleReturn}
            whileHover={{ scale: 1.1 }}
            whileTap={{ scale: 0.9 }}
          >
            ✓
          </motion.button>
          <motion.button
            className="action-btn delete-btn"
            onClick={handleDelete}
            whileHover={{ scale: 1.1 }}
            whileTap={{ scale: 0.9 }}
          >
            ✕
          </motion.button>
        </div>
      )}
    </motion.div>
  )
}

export default BorrowCard
