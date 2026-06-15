# -*- coding: utf-8 -*-
# 搜狐视频 (tv.sohu.com) 爬虫配置

# 指定视频ID列表 (detail模式)
SO_VIDEO_ID_LIST = [
    # "dXMvMzM1MTY3MzA5LzQ3NDg3NjY4OC5zaHRtbA==",
]

# 指定专辑ID列表 (creator/album模式)
SO_ALBUM_ID_LIST = [
    # "12345",
]

# 默认分类 (cid)
# 电视剧=2, 电影=1, 综艺=7, 动漫=4, 纪录片=5, 少儿=10, 音乐=9
SO_CATEGORY = "2"

# 默认排序: 0=综合, 1=最新, 2=最热
SO_SORT_TYPE = "1"

# 搜狐登录 cookie (可选, 浏览内容通常不需要)
SO_LOGIN_COOKIE = ""
