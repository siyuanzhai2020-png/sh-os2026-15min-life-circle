# 社区生活圈智能体检与规划助手 —— 复现链
# Windows 无 make 时用等价的 .\tasks.ps1 <target>

PY ?= python
COMMUNITY ?= caoyang-demo
PORT ?= 8000

.PHONY: help demo report serve test check data clean distclean

help:
	@echo "make demo    生成数据 → 体检 → 报告（默认社区 $(COMMUNITY)）"
	@echo "make serve   启动交互式 Demo http://127.0.0.1:$(PORT)"
	@echo "make test    引擎单元测试"
	@echo "make check   服务端到端自检（需先 make serve）"
	@echo "make data    仅重建数据集"
	@echo "make clean   清理报告输出"

demo:
	$(PY) tools/build_demo.py $(COMMUNITY) --k 2

data:
	$(PY) tools/build_demo.py $(COMMUNITY) --rebuild --no-optimize --out out/_dataonly

report: demo

serve:
	$(PY) tools/serve.py --port $(PORT) --warm

test:
	$(PY) -m unittest discover -s tests -v

check:
	$(PY) tools/check_api.py http://127.0.0.1:$(PORT)

clean:
	$(PY) -c "import shutil,pathlib; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('out').glob('*') if p.is_dir() and p.name != '_keep']"

distclean: clean
	$(PY) -c "import shutil; shutil.rmtree('data/cache', ignore_errors=True)"
