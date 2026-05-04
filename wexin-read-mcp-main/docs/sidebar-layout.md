# Sidebar Layout - 可复用侧边栏布局

基于 StockPulse 项目的侧边栏设计，可复用于其他 Web 项目。

---

## 目录结构

```
├── sidebar.css      # 侧边栏样式（纯 CSS）
├── sidebar.html     # 侧边栏 HTML 结构
├── sidebar.js       # 侧边栏交互逻辑（可选）
└── README.md        # 本文档
```

---

## 设计特点

- **深色主题**：默认 `#18181B` 背景色
- **固定宽度**：228px，可通过 CSS 变量自定义
- **可折叠**：支持折叠到仅显示图标（60px）
- **流畅动画**：hover、active 状态过渡平滑
- **零依赖**：纯 HTML + CSS + Vanilla JS

---

## 设计令牌（Design Tokens）

```css
:root {
  /* 侧边栏颜色 */
  --sidebar-bg: #18181B;
  --sidebar-hover: rgba(255, 255, 255, 0.07);
  --sidebar-active: rgba(255, 255, 255, 0.12);

  /* 侧边栏尺寸 */
  --sidebar-w: 228px;
  --sidebar-collapsed-w: 60px;

  /* 圆角 */
  --r-sm: 6px;
  --r: 12px;

  /* 过渡动画 */
  --t: 0.2s;
  --ease: cubic-bezier(0.4, 0, 0.2, 1);
}
```

---

## HTML 结构

```html
<!-- APP SHELL -->
<div class="app">

  <!-- SIDEBAR -->
  <aside class="sidebar">
    <!-- Logo 区域 -->
    <div class="sidebar-logo">
      <div class="brand">
        <div class="brand-icon">
          <!-- SVG 图标 -->
          <svg viewBox="0 0 16 16" fill="none">
            <path d="M2 12L5.5 7.5L8 10L11 5.5L14 9" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <span>应用名称</span>
      </div>
      <div class="tagline">简短描述文字</div>
    </div>

    <!-- 导航区域 -->
    <nav class="sidebar-nav">
      <!-- 分组标签 -->
      <div class="nav-section-label">分组名称</div>

      <!-- 导航项 -->
      <button class="nav-item active" data-view="home" onclick="switchView('home', this)">
        <svg viewBox="0 0 20 20" fill="none">
          <!-- SVG path -->
        </svg>
        <span>导航文字</span>
        <!-- 可选：徽章 -->
        <span class="nav-badge">3</span>
      </button>

      <!-- 更多导航项... -->
    </nav>

    <!-- 底部状态栏 -->
    <div class="sidebar-footer">
      <div class="status-row">
        <span class="status-dot-sidebar" id="statusDot"></span>
        <span id="statusText">状态文字</span>
      </div>
    </div>
  </aside>

  <!-- MAIN CONTENT -->
  <div class="main">
    <!-- 顶部栏 -->
    <header class="topbar">
      <div>
        <div class="topbar-title" id="topbarTitle">页面标题</div>
        <div class="topbar-subtitle" id="topbarSub">页面副标题</div>
      </div>
      <div class="topbar-sep"></div>
      <!-- 顶部操作按钮 -->
    </header>

    <!-- 内容区域 -->
    <div class="content-wrap">
      <!-- 页面内容 -->
    </div>
  </div>

</div>
```

---

## CSS 样式

### 基础布局

```css
/* App 容器 */
.app {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

/* 侧边栏 */
.sidebar {
  width: var(--sidebar-w);
  background: var(--sidebar-bg);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  overflow: hidden;
  position: relative;
  z-index: 10;
}

/* 主内容区 */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

/* 顶部栏 */
.topbar {
  height: 58px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  padding: 0 28px;
  gap: 16px;
  flex-shrink: 0;
}

/* 内容区 */
.content-wrap {
  flex: 1;
  overflow-y: auto;
  padding: 28px;
}
```

### Logo 区域

```css
.sidebar-logo {
  padding: 22px 20px 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  flex-shrink: 0;
}

.sidebar-logo .brand {
  font-size: 18px;
  font-weight: 700;
  color: #fff;
  letter-spacing: -0.5px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.sidebar-logo .brand-icon {
  width: 30px;
  height: 30px;
  background: var(--red); /* 主题色 */
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.sidebar-logo .brand-icon svg {
  width: 16px;
  height: 16px;
}

.sidebar-logo .tagline {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.35);
  margin-top: 3px;
  letter-spacing: 0.2px;
}
```

### 导航区域

```css
.sidebar-nav {
  padding: 12px 10px;
  flex: 1;
  overflow-y: auto;
}

.sidebar-nav::-webkit-scrollbar {
  display: none; /* 隐藏滚动条 */
}

/* 分组标签 */
.nav-section-label {
  font-size: 10px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.25);
  letter-spacing: 0.8px;
  text-transform: uppercase;
  padding: 10px 10px 6px;
  margin-top: 4px;
}

/* 导航项 */
.nav-item {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 10px 12px;
  border-radius: var(--r-sm);
  cursor: pointer;
  transition: background var(--t) var(--ease), color var(--t) var(--ease);
  color: rgba(255, 255, 255, 0.55);
  font-size: 13.5px;
  font-weight: 500;
  border: none;
  background: none;
  width: 100%;
  text-align: left;
  position: relative;
  user-select: none;
  margin-bottom: 2px;
}

.nav-item svg {
  width: 17px;
  height: 17px;
  flex-shrink: 0;
  opacity: 0.8;
}

.nav-item:hover {
  background: var(--sidebar-hover);
  color: rgba(255, 255, 255, 0.85);
}

.nav-item.active {
  background: var(--sidebar-active);
  color: #fff;
  font-weight: 600;
}

.nav-item.active svg {
  opacity: 1;
}

/* 导航徽章 */
.nav-item .nav-badge {
  margin-left: auto;
  font-size: 10px;
  font-weight: 700;
  background: var(--red);
  color: #fff;
  border-radius: 20px;
  padding: 2px 7px;
  min-width: 20px;
  text-align: center;
}
```

### 底部状态栏

```css
.sidebar-footer {
  padding: 16px 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.07);
  flex-shrink: 0;
}

.sidebar-footer .status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.35);
}

.status-dot-sidebar {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.2);
}

.status-dot-sidebar.online {
  background: #34D399;
  box-shadow: 0 0 6px rgba(52, 211, 153, 0.5);
}
```

### 折叠功能（可选）

```css
/* 折叠状态：只显示图标 */
.sidebar.collapsed {
  width: var(--sidebar-collapsed-w);
}

.sidebar.collapsed .sidebar-logo .brand span,
.sidebar.collapsed .sidebar-logo .tagline,
.sidebar.collapsed .nav-item span,
.sidebar.collapsed .nav-section-label,
.sidebar.collapsed .sidebar-footer {
  display: none;
}
```

### 页面切换动画

```css
/* 视图容器 */
.view {
  display: none;
  animation: viewIn var(--t-md) var(--ease);
}

.view.active {
  display: block;
}

@keyframes viewIn {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
```

---

## JavaScript 交互

```javascript
// 视图切换
function switchView(viewName, btnElement) {
  // 1. 更新导航 active 状态
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.classList.remove('active');
  });
  if (btnElement) {
    btnElement.classList.add('active');
  }

  // 2. 切换视图显示
  document.querySelectorAll('.view').forEach(view => {
    view.classList.remove('active');
  });
  const targetView = document.getElementById('view-' + viewName);
  if (targetView) {
    targetView.classList.add('active');
  }

  // 3. 更新顶部栏标题（可选）
  const titles = {
    'home': { title: '首页', subtitle: '欢迎使用' },
    'task': { title: '文章收集', subtitle: '从微信公众号抓取文章' },
    'config': { title: '系统配置', subtitle: '管理设置' },
  };
  const info = titles[viewName] || { title: viewName, subtitle: '' };
  document.getElementById('topbarTitle').textContent = info.title;
  document.getElementById('topbarSub').textContent = info.subtitle;
}

// 侧边栏折叠
function toggleSidebar() {
  const sidebar = document.querySelector('.sidebar');
  sidebar.classList.toggle('collapsed');
}

// 更新状态指示器
function updateStatus(status) {
  const dot = document.getElementById('serverDot');
  const text = document.getElementById('serverStatus');
  if (dot && text) {
    if (status === 'online') {
      dot.classList.add('online');
      text.textContent = '已连接';
    } else {
      dot.classList.remove('online');
      text.textContent = '未连接';
    }
  }
}
```

---

## 使用示例

### 1. 基本使用

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>我的应用</title>
  <style>
    /* 复制上述 CSS 到此处 */
  </style>
</head>
<body>
  <!-- 复制上述 HTML 结构 -->
  <script>
    /* 复制上述 JavaScript 到此处 */
  </script>
</body>
</html>
```

### 2. 在现有项目中集成

```html
<!-- 在现有 index.html 中添加 -->
<link rel="stylesheet" href="sidebar.css">

<!-- 复制 HTML 结构到 <body> -->
<!-- 添加 switchView 函数 -->
<!-- 在需要的元素上添加 onclick="switchView('viewName', this)" -->
```

---

## 自定义指南

### 1. 修改主题色

```css
:root {
  --sidebar-bg: #1a1a2e;  /* 深蓝紫色 */
  --sidebar-hover: rgba(255, 255, 255, 0.1);
  --sidebar-active: rgba(255, 255, 255, 0.15);
}
```

### 2. 修改侧边栏宽度

```css
:root {
  --sidebar-w: 260px;        /* 加宽 */
  --sidebar-collapsed-w: 72px;
}
```

### 3. 修改字体

```css
.sidebar {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}
```

### 4. 添加更多导航分组

```html
<div class="nav-section-label">数据分析</div>
<button class="nav-item" data-view="analytics">
  <svg><!-- ... --></svg>
  <span>数据分析</span>
</button>
```

---

## 注意事项

1. **可访问性**：导航按钮使用 `<button>` 标签，支持键盘焦点
2. **响应式**：在移动端建议隐藏侧边栏或使用汉堡菜单
3. **性能**：CSS 变量便于主题切换，无需重写样式
4. **兼容性**：使用标准 CSS flexbox，支持所有现代浏览器

---

## 许可证

MIT License - 可自由使用于个人和商业项目
