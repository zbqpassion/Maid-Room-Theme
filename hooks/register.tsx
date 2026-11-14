import type { Register, ThemeSetArgs } from 'claude-code'

import { makeColors } from './make-colors.js'
import { SCENE } from './scene.js'

// 只做浅色：背景是暖色调的浅色插画（奶油色房间、橙发女仆），
// 深色模式下浅底配浅字会看不清，所以用 only: 'light' 锁定浅色。
// 配色取自插画：奶油色底、深棕文字、发色的橙做强调，红绿黄保留各自含义
const { theme, problems } = makeColors({
  label: 'Maid room',
  only: 'light',
  roles: { accent: '#d9651f', danger: '#c8402f', success: '#4f8a45', warning: '#b07a12', pro: '#8a5fb0' },
  light: {
    gray: {
      anchors: {
        'surface-0': '#f3e7db',
        'surface-1': '#f8efe6',
        'surface-2': '#fdf8f2',
        'text-primary': '#2b1c15',
        'text-secondary': '#523f36',
        'text-muted': '#6b564b',
      },
    },
  },
})

const SCENE_NAME = 'maid-room'
const STATUS = '女仆小屋 · 慢慢来'

// 两种背景画法：
// vector（默认）：纯矢量场景。桌面端的背景场景只画矢量，插画由 tools/build_scene.py 描摹成路径，
//   受 131072 字符上限限制，细节是简化过的，但一定能显示。
// image：直接用位图 assets/backdrop.jpg（tools/compose_backdrop.py 合成），画质是原图水平；
//   有的 App 版本不画图片背景，那时会退回 App 自带的底色，用 /maid-room vector 切回来即可。
type Mode = 'vector' | 'image'

// 聊天栏、侧栏都是盖在背景上的毛玻璃面板，人物有一大半在聊天栏底下。
// veil（0 到 100）是这层面板的浓度：越小人物越清楚，越大文字越好读。
// 默认 35；模糊跟着浓度走（每 20 加 1），浓度低时不模糊，面板内外的人物才不会一半清楚一半糊。
const DEFAULT_VEIL = 35

const look = (mode: Mode, veil: number): ThemeSetArgs =>
  mode === 'image'
    ? { colors: theme, backdrop: { image: { source: 'assets/backdrop.jpg' } } }
    : {
        colors: theme,
        backdrop: { scene: SCENE_NAME, glass: { blur: Math.round(veil / 20), opacity: veil / 100 } },
      }

const isMode = (value: unknown): value is Mode => value === 'vector' || value === 'image'

const isVeil = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 100

const USAGE =
  '用法：/maid-room veil 0到100（聊天栏面板的浓度，越小人物越清楚，越大文字越好读）；/maid-room vector（矢量背景，一定能显示）；/maid-room image（位图原画，部分 App 版本不显示）'

// 读出上次保存的选择（跨会话保留），没存过或存坏了就用默认值
const stored = async ($: { store: { get: (key: string) => Promise<unknown> } }) => {
  const mode = await $.store.get('mode')
  const veil = await $.store.get('veil')
  const settings: { mode: Mode; veil: number } = {
    mode: isMode(mode) ? mode : 'vector',
    veil: isVeil(veil) ? veil : DEFAULT_VEIL,
  }

  return settings
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    // 先设主题：打开会话时界面在等它，越早发出去，默认外观停留得越短。
    // 读保存的设置是本地文件，很快；注册命令、弹提示都不影响画面，放到后面。
    // 会话启动时客户端可能还没连上，isDrawn 会是 false，但连上后照样会画
    const now = await stored($)
    await $.theme.set(look(now.mode, now.veil))
    $.ui.status(STATUS)

    // 如果有一行会让桌面端整套主题不上色，弹个提示
    const unpainted = problems.find(line => line.startsWith("Desktop won't paint"))

    if (unpainted !== undefined) {
      $.ui.toast(unpainted)
    }
    await $.command.register({
      name: 'maid-room',
      description: '调整女仆小屋主题：veil 0到100 调人物清晰度，vector 或 image 切换背景画法',
    })

    return next(e)
  })

  on('command.run', { command: 'maid-room' }, async ($, e) => {
    const [word = '', value = ''] = e.args.trim().toLowerCase().split(/\s+/)
    const now = await stored($)

    if (word === 'veil' && isVeil(Number(value)) && value !== '') {
      now.veil = Number(value)
      await $.store.set('veil', now.veil)
    } else if (isMode(word)) {
      now.mode = word
      await $.store.set('mode', now.mode)
    } else {
      return { text: `当前：背景 ${now.mode}，面板浓度 ${now.veil}。${USAGE}` }
    }
    const set = await $.theme.set(look(now.mode, now.veil))

    if (!set.isDrawn) {
      return { text: `已保存（背景 ${now.mode}，面板浓度 ${now.veil}），但暂未绘制：${set.reason}` }
    }
    if (word === 'image') {
      return {
        text: '已切到位图原画。如果背景变成了 App 自带的底色、看不到人物，说明这个版本不画图片背景，用 /maid-room vector 切回矢量。',
      }
    }

    return { text: `已应用：背景 ${now.mode}，面板浓度 ${now.veil}。` }
  })

  on('ui.render', { component: 'Backdrop' }, ($, e, next) => {
    if ((e.surface !== 'desktop' && e.surface !== 'mobile') || e.props.scene !== SCENE_NAME) {
      return next(e)
    }

    const { Svg } = $.ui.resolve(e)

    // 不开 isInteractive：按一张图绘制，静态、不占 CPU
    return (
      <Svg
        source={SCENE}
        alt="奶油色房间插画：右下角一位橙色长发、穿黑白女仆装的少女坐在床边，正在整理白色长袜，身后是柔焦的卧室"
      />
    )
  })
}
