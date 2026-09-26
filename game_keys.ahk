#Requires AutoHotkey v1.1
#SingleInstance Force
#MaxThreadsPerHotkey 2
SetKeyDelay, 0, 50
SetMouseDelay, -1

; ========================================
; 极品飞车集结 - 按键映射脚本
; 功能：Python发送F1 -> AHK拦截 -> 转为I键
; 说明：AHK模拟的按键更容易被游戏接收
; ========================================

; ---- F1 -> I 键（技能） ----
; 按下F1时按住I，松开F1时松开I
$F1::
    SendInput, {i down}
    return

$F1 up::
    SendInput, {i up}
    return

; ---- F2 -> 暂停/恢复脚本 ----
F2::Suspend

; ---- 启动提示 ----
#NoTrayIcon
Menu, Tray, Tip, NFS Auto 按键助手 (F1=I技能, F2=暂停)
