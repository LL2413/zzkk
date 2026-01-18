import React, { useState } from 'react'
import { motion } from 'framer-motion'
import { format } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import useStore from '../store/useStore'
import BorrowCard from '../components/BorrowCard'
import './HistoryPage.css'

function HistoryPage() {
  const items = useStore(state => state.items)
  const [filter, setFilter] = useState('all') // all | returned | active
  
  const filteredItems = items.filter(item => {
    if (filter === 'all') return true
    return item.status === filter
  })
  
  // 按日期分组
  const groupedItems = filteredItems.reduce((groups, item) => {
    const date = format(new Date(item.createdAt), 'yyyy-MM-dd')
    if (!groups[date]) {
      groups[date] = []
    }
    groups[date].push(item)
    return groups
  }, {})
  
  const sortedDates = Object.keys(groupedItems).sort((a, b) => 
    new Date(b).getTime() - new Date(a).getTime()
  )

  const formatDateHeader = (dateStr) => {
    const date = new Date(dateStr)
    const today = new Date()
    const yesterday = new Date(today)
    yesterday.setDate(yesterday.getDate() - 1)
    
    if (format(date, 'yyyy-MM-dd') === format(today, 'yyyy-MM-dd')) {
      return '今天'
    }
    if (format(date, 'yyyy-MM-dd') === format(yesterday, 'yyyy-MM-dd')) {
      return '昨天'
    }
    return format(date, 'M月d日 EEEE', { locale: zhCN })
  }

  return (
    <div className="page history-page">
      <header className="page-header">
        <motion.h1 
          className="page-title"
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          借用记录
        </motion.h1>
        <motion.p 
          className="page-subtitle"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.1 }}
        >
          查看你的所有借用历史
        </motion.p>
      </header>

      {/* 筛选标签 */}
      <motion.div 
        className="filter-tabs"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
      >
        <button 
          className={`filter-tab ${filter === 'all' ? 'active' : ''}`}
          onClick={() => setFilter('all')}
        >
          全部 ({items.length})
        </button>
        <button 
          className={`filter-tab ${filter === 'active' ? 'active' : ''}`}
          onClick={() => setFilter('active')}
        >
          进行中 ({items.filter(i => i.status === 'active').length})
        </button>
        <button 
          className={`filter-tab ${filter === 'returned' ? 'active' : ''}`}
          onClick={() => setFilter('returned')}
        >
          已归还 ({items.filter(i => i.status === 'returned').length})
        </button>
      </motion.div>

      {/* 记录列表 */}
      <div className="history-list">
        {sortedDates.length > 0 ? (
          sortedDates.map((date, dateIndex) => (
            <motion.div 
              key={date} 
              className="date-group"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: dateIndex * 0.1 + 0.3 }}
            >
              <h3 className="date-header">{formatDateHeader(date)}</h3>
              <div className="items-list">
                {groupedItems[date].map((item, index) => (
                  <motion.div
                    key={item.id}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: index * 0.05 }}
                  >
                    <BorrowCard item={item} />
                  </motion.div>
                ))}
              </div>
            </motion.div>
          ))
        ) : (
          <motion.div 
            className="empty-state"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
          >
            <div className="empty-icon">📝</div>
            <h3 className="empty-title">暂无记录</h3>
            <p className="empty-description">
              点击下方 + 按钮添加你的第一条借用记录
            </p>
          </motion.div>
        )}
      </div>
    </div>
  )
}

export default HistoryPage
