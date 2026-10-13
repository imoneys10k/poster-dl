# 图片源文件

`assets/` 里的图都由这里的 HTML 用 Chrome 截图生成,改完重新渲染即可。地址后加 `#en` 出英文版。

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
render() {  # render <源文件[#en]> <高度> <输出>
  "$CHROME" --headless=new --hide-scrollbars --default-background-color=00000000 \
    --force-device-scale-factor=2 --window-size=860,$2 --screenshot="$3" "file://$PWD/$1"
}
render hero.html     470  ../hero.png
render hero.html#en  470  ../hero-en.png
render wall.html     1100 ../wall.png      # 渲染后用 `magick wall.png -trim +repage wall.png` 裁掉多余高度
render demo.html     700  ../demo.png      # 同上
```

社交预览图(1280×640)用 `hero.html#en-social`,窗口 860×430,缩放系数 1.4883721。

用到的字体都是 macOS 自带的(DIN Condensed、Snell Roundhand、宋体-简、苹方),等宽字体是 JetBrains Mono。
