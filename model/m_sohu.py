# -*- coding: utf-8 -*-
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
# 声明：本代码仅供学习和研究目的使用。

from pydantic import BaseModel, Field


class SohuVideoUrlInfo(BaseModel):
    """搜狐视频 URL 信息"""
    vid: str = Field(title="视频ID", description="视频唯一标识")
    url_type: str = Field(default="normal", title="URL类型", description="normal / short / album")


class SohuAlbumUrlInfo(BaseModel):
    """搜狐专辑 URL 信息"""
    album_id: str = Field(title="专辑ID", description="专辑唯一标识")
