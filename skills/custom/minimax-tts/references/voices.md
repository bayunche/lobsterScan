# MiniMax TTS · 中文 voice 速查

> 速度/音量/音高都能在 voice_setting 里调；voice_id 决定音色。

## 男声（汇报默认）

| voice_id | 适合场景 |
| --- | --- |
| `male-qn-qingse` | **默认**。沉稳清越，正式汇报 |
| `male-qn-jingying` | 精英商务，活动开场 |
| `male-qn-badao` | 霸道总裁，强势讲述 |
| `male-qn-daxuesheng` | 大学生，年轻沉稳 |
| `presenter_male` | 主持人男声 |
| `audiobook_male_1` | 有声书叙述男 |

## 女声

| voice_id | 适合场景 |
| --- | --- |
| `female-shaonv` | 少女音，活泼 |
| `female-yujie` | 御姐，沉稳清亮 |
| `female-chengshu` | 成熟女声，权威 |
| `female-tianmei` | 甜美 |
| `presenter_female` | 主持人女声 |
| `audiobook_female_1` | 有声书叙述女 |

## 默认参数
```json
{
  "voice_setting": {
    "voice_id": "male-qn-qingse",
    "speed": 1.0,         // 0.5 - 2.0
    "vol":   1.0,         // 0 - 10
    "pitch": 0            // -12 - 12
  },
  "audio_setting": {
    "sample_rate": 32000,
    "bitrate": 128000,
    "format": "mp3"
  }
}
```

## 模型选择

| model | 说明 |
| --- | --- |
| `speech-02-hd` | 默认，高质量 |
| `speech-02-turbo` | 更快，质量略低 |
| `speech-01-hd` / `speech-01-turbo` | 旧版 |
