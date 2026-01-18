import React, { useState } from 'react'
import { motion } from 'framer-motion'
import useStore, { BORROW_TYPES } from '../store/useStore'
import './AddPage.css'

function AddPage({ onComplete }) {
  const addItem = useStore(state => state.addItem)
  
  const [selectedType, setSelectedType] = useState('charger')
  const [note, setNote] = useState('')
  const [location, setLocation] = useState('')
  const [reminderMinutes, setReminderMinutes] = useState(60)
  const [customReminder, setCustomReminder] = useState(false)
  
  const selectedTypeData = BORROW_TYPES.find(t => t.id === selectedType)
  
  const quickReminderOptions = [
    { label: '15分钟', value: 15 },
    { label: '30分钟', value: 30 },
    { label: '1小时', value: 60 },
    { label: '2小时', value: 120 },
    { label: '今天', value: 'today' },
    { label: '自定义', value: 'custom' },
  ]
  
  const handleSubmit = (e) => {
    e.preventDefault()
    
    let reminderTime = null
    if (reminderMinutes === 'today') {
      // 今天结束前（晚上8点）
      const today = new Date()
      today.setHours(20, 0, 0, 0)
      if (today.getTime() < Date.now()) {
        today.setDate(today.getDate() + 1)
      }
      reminderTime = today.toISOString()
    } else if (typeof reminderMinutes === 'number') {
      reminderTime = new Date(Date.now() + reminderMinutes * 60 * 1000).toISOString()
    }
    
    addItem({
      type: selectedType,
      note: note.trim(),
      location: location.trim(),
      reminderTime,
    })
    
    // 重置表单
    setNote('')
    setLocation('')
    setReminderMinutes(60)
    setCustomReminder(false)
    
    // 返回首页
    onComplete?.()
  }

  return (
    <div className="page add-page">
      <header className="page-header">
        <motion.h1 
          className="page-title"
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
        >
          添加借用
        </motion.h1>
        <motion.p 
          className="page-subtitle"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.1 }}
        >
          记录你借用的物品，我们会及时提醒你归还
        </motion.p>
      </header>

      <form className="add-form" onSubmit={handleSubmit}>
        {/* 类型选择 */}
        <motion.div 
          className="form-section"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          <label className="form-label">借用类型</label>
          <div className="type-grid">
            {BORROW_TYPES.map((type) => (
              <motion.button
                key={type.id}
                type="button"
                className={`type-item ${selectedType === type.id ? 'selected' : ''}`}
                onClick={() => {
                  setSelectedType(type.id)
                  setReminderMinutes(type.defaultDuration)
                }}
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
              >
                <span className="type-icon">{type.icon}</span>
                <span className="type-name">{type.name}</span>
              </motion.button>
            ))}
          </div>
        </motion.div>

        {/* 提醒时间 */}
        <motion.div 
          className="form-section"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <label className="form-label">提醒时间</label>
          <div className="reminder-options">
            {quickReminderOptions.map((option) => (
              <button
                key={option.value}
                type="button"
                className={`reminder-btn ${
                  option.value === 'custom' 
                    ? customReminder ? 'selected' : ''
                    : reminderMinutes === option.value && !customReminder ? 'selected' : ''
                }`}
                onClick={() => {
                  if (option.value === 'custom') {
                    setCustomReminder(true)
                  } else {
                    setCustomReminder(false)
                    setReminderMinutes(option.value)
                  }
                }}
              >
                {option.label}
              </button>
            ))}
          </div>
          
          {customReminder && (
            <motion.div 
              className="custom-reminder"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
            >
              <input
                type="number"
                className="form-input"
                placeholder="输入分钟数"
                value={typeof reminderMinutes === 'number' ? reminderMinutes : ''}
                onChange={(e) => setReminderMinutes(parseInt(e.target.value) || 60)}
                min="1"
              />
              <span className="custom-unit">分钟后提醒</span>
            </motion.div>
          )}
        </motion.div>

        {/* 借用地点 */}
        <motion.div 
          className="form-section"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          <label className="form-label">借用地点 (可选)</label>
          <input
            type="text"
            className="form-input"
            placeholder="如：星巴克咖啡厅"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
        </motion.div>

        {/* 备注 */}
        <motion.div 
          className="form-section"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
        >
          <label className="form-label">备注 (可选)</label>
          <textarea
            className="form-textarea"
            placeholder="添加一些备注信息..."
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
          />
        </motion.div>

        {/* 提交按钮 */}
        <motion.button
          type="submit"
          className="submit-btn"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          <span className="btn-icon">✓</span>
          开始计时
        </motion.button>
      </form>
    </div>
  )
}

export default AddPage
