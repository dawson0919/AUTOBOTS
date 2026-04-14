---
title: Nano Banana 2 (AI Image Gen)
tags: [project, ai-image, gemini]
project: nano-banana-2-skill
status: installed
---

# 🍌 Nano Banana 2

AI 圖像生成 CLI（Gemini 3.1 Flash Image Preview / Gemini 3 Pro），支援多解析度、reference image、green screen 透明背景。

## 📦 已安裝
- CLI：`C:\Users\User\.bun\bin\nano-banana.exe`
- Claude Skill：`~/.claude/skills/nano-banana/`
- 來源：https://github.com/kingbootoshi/nano-banana-2-skill
- API Key：`~/.nano-banana/.env`（Gemini API）
- ImageMagick 7.1.2 + FFmpeg（透明去背用）

## 🚀 使用範例
```bash
# 基本生成
nano-banana "a neon cyberpunk NBA logo"

# 指定輸出資料夾（已 alias 到 Downloads）
nano-banana "futuristic robot" -d /c/Users/User/Downloads

# 16:9 簡報用
nano-banana "presentation cover" -a 16:9 --output slide-cover

# 用參考圖編輯
nano-banana "edit darker" -r input.png

# 透明 PNG（綠幕去背）
nano-banana "sticker mascot" -t

# 4K 高解析度
nano-banana "wallpaper" -s 4K
```

## 💰 成本
- 每張圖約 **$0.09** USD（Gemini 3.1 Flash）
- 已生成：6 張簡報封面（共 ~$0.56）

## 🔗 已生成圖庫
存於 `C:\Users\User\Downloads\slide-*.jpeg`

## 🔗 相關
- [[topics/infrastructure]] — API key 管理
