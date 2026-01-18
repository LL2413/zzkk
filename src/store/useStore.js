import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { Capacitor } from '@capacitor/core'
import { LocalNotifications } from '@capacitor/local-notifications'

// 预设的借用类型
export const BORROW_TYPES = [
  { id: 'charger', name: '充电宝', icon: '🔋', defaultDuration: 60 }, // 默认60分钟
  { id: 'bike', name: '共享单车', icon: '🚲', defaultDuration: 30 },
  { id: 'umbrella', name: '共享雨伞', icon: '☔', defaultDuration: 120 },
  { id: 'book', name: '图书', icon: '📚', defaultDuration: 1440 * 30 }, // 30天
  { id: 'car', name: '共享汽车', icon: '🚗', defaultDuration: 60 },
  { id: 'other', name: '其他', icon: '📦', defaultDuration: 60 },
]

// 检查是否在原生环境
const isNative = Capacitor.isNativePlatform()

const useStore = create(
  persist(
    (set, get) => ({
      // 借用记录列表
      items: [],
      
      // 添加借用记录
      addItem: (item) => {
        const newItem = {
          id: Date.now().toString(),
          ...item,
          createdAt: new Date().toISOString(),
          status: 'active', // active | returned
        }
        set((state) => ({
          items: [newItem, ...state.items]
        }))
        
        // 设置提醒
        if (item.reminderTime) {
          get().scheduleNotification(newItem)
        }
        
        return newItem
      },
      
      // 标记为已归还
      returnItem: (id) => {
        // 取消该物品的通知
        get().cancelNotification(id)
        
        set((state) => ({
          items: state.items.map(item =>
            item.id === id
              ? { ...item, status: 'returned', returnedAt: new Date().toISOString() }
              : item
          )
        }))
      },
      
      // 删除记录
      deleteItem: (id) => {
        // 取消该物品的通知
        get().cancelNotification(id)
        
        set((state) => ({
          items: state.items.filter(item => item.id !== id)
        }))
      },
      
      // 更新记录
      updateItem: (id, updates) => {
        set((state) => ({
          items: state.items.map(item =>
            item.id === id ? { ...item, ...updates } : item
          )
        }))
      },
      
      // 获取活跃的借用
      getActiveItems: () => {
        return get().items.filter(item => item.status === 'active')
      },
      
      // 获取历史记录
      getHistoryItems: () => {
        return get().items.filter(item => item.status === 'returned')
      },
      
      // 调度通知
      scheduleNotification: async (item) => {
        const type = BORROW_TYPES.find(t => t.id === item.type)
        const title = '还了吗？⏰'
        const body = `该归还${type?.name || '物品'}了！${item.note ? '\n' + item.note : ''}`
        
        if (isNative) {
          // 使用 Capacitor 本地通知
          try {
            await LocalNotifications.schedule({
              notifications: [
                {
                  id: parseInt(item.id),
                  title,
                  body,
                  schedule: { at: new Date(item.reminderTime) },
                  sound: 'default',
                  actionTypeId: '',
                  extra: { itemId: item.id }
                }
              ]
            })
          } catch (e) {
            console.error('Failed to schedule notification:', e)
          }
        } else if ('Notification' in window) {
          // Web 通知
          const reminderTime = new Date(item.reminderTime).getTime()
          const now = Date.now()
          const delay = reminderTime - now
          
          if (delay > 0) {
            setTimeout(() => {
              if (Notification.permission === 'granted') {
                new Notification(title, {
                  body,
                  icon: '/favicon.svg',
                  tag: item.id,
                  requireInteraction: true,
                })
              }
            }, delay)
          }
        }
      },
      
      // 取消通知
      cancelNotification: async (id) => {
        if (isNative) {
          try {
            await LocalNotifications.cancel({
              notifications: [{ id: parseInt(id) }]
            })
          } catch (e) {
            console.error('Failed to cancel notification:', e)
          }
        }
      },
      
      // 请求通知权限
      requestNotificationPermission: async () => {
        if (isNative) {
          try {
            const result = await LocalNotifications.requestPermissions()
            return result.display === 'granted'
          } catch (e) {
            console.error('Failed to request permission:', e)
            return false
          }
        } else if ('Notification' in window) {
          const permission = await Notification.requestPermission()
          return permission === 'granted'
        }
        return false
      },
    }),
    {
      name: 'huailema-storage',
    }
  )
)

export default useStore
