# 体检报告：服务运行状态（v3）

## 1. Python 进程

```
python.exe                   7512 Console                    1     49,044 K
python.exe                   15468 Console                    1      1,176 K
python.exe                   22920 Console                    1    214,872 K
python.exe                   14064 Console                    1      1,128 K
python.exe                   33804 Console                    1      3,028 K
python.exe                   25952 Console                    1      1,864 K
python.exe                    7216 Console                    1      6,340 K
python.exe                   43240 Console                    1      5,144 K
python.exe                   32592 Console                    1    174,044 K
```

共 9 个 Python 进程，总内存约 459 MB。

## 2. Node 进程

```
node.exe                     40876 Console                    1     31,360 K
node.exe                     36468 Console                    1     34,932 K
node.exe                     40584 Console                    1    966,772 K
node.exe                     43032 Console                    1     39,628 K
```

共 4 个 Node 进程，总内存约 1.07 GB（其中 PID 40584 占 966 MB）。

## 3. 数据库文件

```
2026/07/29  23:05         4,390,912 takton-alpha-dev.db
```

文件大小 4.19 MB，最后修改时间 2026-07-29 23:05。

## 总结

Python/Node 进程均在运行，DB 文件存在且刚刚更新（23:05），服务状态正常。
