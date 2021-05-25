#!/usr/bin/env python
from __future__ import annotations
import sys
import typing
from PyQt5.Qt import *
from PyQt5 import QtCore
from uuid import uuid4
from subprocess import check_call
from shutil import which
from functools import partial
from qtmodern.styles import light, dark
from qtmodern.windows import ModernWindow

# ModernWindow = lambda x: x
import pyperclip
import json
import os
import pydantic
import pathlib
import wisepy2

default_color = QColor(200, 100, 100)
empty_seq = []


def uuid_str():
    return uuid4().hex


class Attr(pydantic.BaseModel):
    id: str
    name: str
    typ: str
    color: str

    @property
    def item_name(self):
        return self.name

    @property
    def item_color(self):
        return self.color

    @property
    def item_id(self):
        return self.id


class Document(pydantic.BaseModel):
    id: str
    name: str
    path: str
    attrs: dict[str, Value]

    @property
    def item_name(self):
        return self.name

    @property
    def item_id(self):
        return self.id

class QueryProxy:
    def __init__(self, doc: Document, attrs: Attrs, attr_lookup: dict):
        self.doc = doc
        self.attrs = attrs
        self.attr_lookup = attr_lookup
    def __getattr__(self, attr):
        if attr == "名字":
            return self.doc.name
        elif attr == "文件路径":
            return self.doc.path
        if attr_id := self.attr_lookup.get(attr, None):
            return self.doc.attrs[attr_id]
        found = next((k for k,v in self.attrs.items() if v.name == attr), None)
        if found:
            attr_id = self.attr_lookup[attr] = self.attrs[found].id
            return self.doc.attrs[attr_id]
        return None

Value = typing.Union[int, str, float]

Attr.update_forward_refs()
Document.update_forward_refs()
Docs = dict[str, Document]
Attrs = dict[str, Attr]


class Data(pydantic.BaseModel):
    docs: Docs
    attrs: Attrs
    editor: str

    @staticmethod
    def empty():
        return Data(docs={}, attrs={}, editor="notepad")


Data.update_forward_refs()


class Project:
    datafile: str

    def __init__(self, datafile: str):
        self.datafile = datafile

    def save(self, data: Data):
        json.dump(
            data.dict(),
            pathlib.Path(self.datafile).absolute().open('w', encoding='utf-8'),
            ensure_ascii=False,
        )

    def load(self):
        if os.path.exists(self.datafile):
            try:
                data = json.load(
                    open(self.datafile, mode="r", encoding='utf-8')
                )
            except json.JSONDecodeError:
                return Data(docs={}, attrs={}, editor="notepad")

            return Data.parse_obj(data)
        return Data(docs={}, attrs={}, editor="notepad")


T = typing.TypeVar("T")


class Datum(typing.Generic[T]):
    def __init__(self, v: T):
        self.v = v
        self._subscribers = set()

    def subscribe(self, f):
        self._subscribers.add(f)

    def unsubscribe(self, f):
        try:
            self._subscribers.remove(f)
        except KeyError:
            pass

    def notify(self):
        for sub in self._subscribers:
            sub()
    
    def __getattr__(self, item):
        return getattr(self.v, item, None)

_cnt = 0


def new_id():
    global _cnt
    a = _cnt = _cnt + 1
    return a


def connect(clicked: typing.Any, f):
    clicked.connect(f)


def add_widget(layout: QLayout, a: QWidget):
    layout.addWidget(a)


def add_widget_grid(layout: QGridLayout, a: QWidget, c: int, d: int):
    # noinspection PyArgumentList
    layout.addWidget(a, c, d)


class Resizable:
    resize_event = QtCore.pyqtSignal(int)
    last_size = 0

    def resizeEvent(self: QWidget, event: QResizeEvent):
        self.resize_event.emit(1)


class Clickable:
    left_click = pyqtSignal()
    right_click = pyqtSignal()

    def mousePressEvent(self, e):
        btn = e.button()
        if btn == Qt.LeftButton:
            self.left_click.emit()
        elif btn == Qt.RightButton:
            self.right_click.emit()


class DClickableButton(QLabel, Clickable):
    pass


class DInput(QWidget):
    def __init__(self, tip: str, *args):
        super().__init__(*args)
        grid = self.grid = QGridLayout()
        grid.setSpacing(5)
        grid.setContentsMargins(0, 0, 0, 0)
        self.clickable_label = DClickableButton(self)
        self.clickable_label.setText(tip)
        hint = self.clickable_label.sizeHint()
        self.clickable_label.setFixedSize(hint)
        self.register = QLineEdit(self)
        # self.register.setText("")
        self.register.setMaximumWidth(300)
        add_widget_grid(grid, self.clickable_label, 0, 0)
        add_widget_grid(grid, self.register, 0, 1)
        self.setLayout(grid)
        grid.setAlignment(Qt.AlignTop)

        connect(self.clickable_label.left_click, self.fill)

    # noinspection PyArgumentList
    def fill(self):
        text, ok = QInputDialog.getText(
            self, self.clickable_label.text(), "确认"
        )
        if ok:
            self.register.setText(text)


class DPushButton(QPushButton):
    def mousePressEvent(self, e):
        pass


class DListItem(QPushButton):
    def __init__(self, datum: Datum):
        super().__init__(*empty_seq)
        self.datum = datum
        self.sync()

    def sync(self):
        datum = self.datum
        if item_name := datum.item_name:
            self.setText(item_name)
        if item_color := datum.item_color:
            colorize(self, item_color)

    def mousePressEvent(self, e):
        btn = e.button()
        if btn == Qt.LeftButton:
            (f := getattr(self, "on_left_click", None)) and f()
        elif btn == Qt.RightButton:
            (f := getattr(self, "on_right_click", None)) and f()


class DList(QWidget, Resizable, Clickable):
    def __init__(
        self,
        *args,
        layout: typing.Optional[QLayout] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.data: list[Datum] = []
        self.layout = layout or QVBoxLayout()
        self.layout.setSpacing(5)
        self.layout.setContentsMargins(2, 2, 2, 2)

        self.widgets: dict[Datum, DListItem] = {}
        self.setLayout(self.layout)
        connect(self.resize_event, self.resize_items)
        self.item_on_left_click = None
        self.item_on_right_click = None

    def resize_items(self):
        width = self.width()
        for each in self.widgets.values():
            each.setFixedWidth(int(width * 0.93))

    def elements(self):
        return [a.v for a in self.data]

    def _mk_item_on_left_click(self, bnt):
        return lambda: self.item_on_left_click and self.item_on_left_click(bnt)

    def _mk_item_on_right_click(self, bnt):
        return lambda: self.item_on_right_click and self.item_on_right_click(
            bnt
        )

    def add(self, datum: Datum):
        w = DListItem(datum)
        datum.subscribe(w.sync)
        # noinspection PyArgumentList
        self.layout.addWidget(w)
        self.widgets[datum] = w
        self.data.append(datum)
        w.on_left_click = self._mk_item_on_left_click(w)
        w.on_right_click = self._mk_item_on_right_click(w)
        w.setFixedWidth(int(self.width() * 0.93))
        return w

    def remove(self, datum: typing.Union[Datum, DListItem]):
        if isinstance(datum, DListItem):
            datum = datum.datum
        try:
            w = self.widgets[datum]
        except ValueError:
            return
        self.layout.removeWidget(w)
        self.data.remove(datum)
        datum.unsubscribe(w.sync)
        del self.widgets[datum]
        w.deleteLater()

    def clear(self):
        for e in self.data:
            e.unsubscribe(self.widgets[e].sync)
        self.data.clear()
        for each in self.widgets.values():
            self.layout.removeWidget(each)
            each.deleteLater()
        self.widgets.clear()


# noinspection PyArgumentList
def separator():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def proper_sized(a: QWidget):
    a.resize(a.sizeHint())


def colorize(a: QWidget, color: str):
    a.setStyleSheet(
        a.styleSheet()
        + f" background-color: {color};"
    )


class DocAttrs(DList):
    def __init__(self, main: Main):
        super().__init__()
        self.main = main

    def add(self, a: Datum[Attr]):
        v = a.v
        self.main.data.attrs[v.id] = v
        super().add(a)

    def remove(self, w: typing.Union[DListItem[Attr], Datum[Attr]]):
        if isinstance(w, DListItem):
            w = w.datum
        del self.main.data.attrs[w.v.id]
        super().remove(w)

    def clear(self):
        for each in self.data:
            del self.main.data.attrs[each.v.id]
        super().clear()


def unparse_type(t: type):
    if t is int:
        return "整数"
    elif t is float:
        return "浮点数"
    elif t is str:
        return "字符串"
    else:
        raise TypeError


def parse_type(s: str):
    if s == "整数":
        return int
    elif s == "浮点数":
        return float
    elif s == "字符串":
        return str
    else:
        raise TypeError


class ChangeDocAttr(QDialog):
    def __init__(self, obj: Datum[Document], glob_attrs: dict[str, Attr]):
        super().__init__(*empty_seq)
        self.glob_attrs = glob_attrs
        self.obj = obj
        obj_attrs = obj.v.attrs
        self.setWindowTitle("文档属性修改器")
        self.layout = QFormLayout()
        self.layout.setLabelAlignment(Qt.AlignHCenter | Qt.AlignCenter)
        self.layout.setSpacing(5)
        self.register_name_input = QLineEdit()
        register_name_label = QLabel("文档显示名")
        proper_sized(register_name_label)
        self.layout.addRow(register_name_label, self.register_name_input)

        self.register_name_input.setText(obj.v.item_name)
        add_attr_for_doc = QPushButton("为文档添加新属性")
        self.layout.addWidget(add_attr_for_doc)
        funcs = []

        def get_kv(attr_id: str, typ: type, line_edit: QLineEdit):
            def app():
                text = line_edit.text()
                if not text.strip():
                    return attr_id, None

                try:
                    v = typ(text)
                except ValueError:
                    msg_box = QMessageBox()
                    msg_box.setText(
                        f"属性「{glob_attrs[attr_id].name}」要求类型{unparse_type(typ)}: {text}"
                    )
                    msg_box.exec_()
                    return None
                return attr_id, v

            return app

        def add_field(attr_id, each_value):
            attr_input = QLineEdit()
            attr_input.setText(str(each_value))
            attr = glob_attrs[attr_id]
            label = DPushButton(attr.name)
            proper_sized(label)
            colorize(label, attr.color)
            funcs.append(get_kv(attr_id, parse_type(attr.typ), attr_input))
            self.layout.addRow(label, attr_input)

        not_added_attrs = set(glob_attrs.keys())
        for attr_id in list(obj_attrs.keys()):
            if attr_id not in glob_attrs:
                del obj_attrs[attr_id]
                continue

            add_field(attr_id, obj_attrs[attr_id])
            not_added_attrs.remove(attr_id)
        not_added_attrs = list(not_added_attrs)

        def add_new_field():
            window = QDialog()
            layout = QFormLayout()
            layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
            checkboxes = []
            for attr_id in not_added_attrs:
                attr = glob_attrs[attr_id]
                btn = QCheckBox(attr.name, window)
                colorize(btn, attr.color)
                layout.addWidget(btn)
                checkboxes.append(btn)
            enter = QPushButton("确定")
            proper_sized(enter)
            layout.addWidget(enter)
            window.setLayout(layout)
            window.setMinimumWidth(150)
            window.setMinimumHeight(100)

            def add_fields():
                for attr_id, e in zip(not_added_attrs, checkboxes):
                    if e.checkState() != 2:
                        continue
                    add_field(attr_id, "")
                window.close()

            connect(enter.clicked, add_fields)
            window.exec_()

        connect(add_attr_for_doc.clicked, add_new_field)

        self.setLayout(self.layout)
        self.setFixedSize(self.sizeHint())
        self.layout.setSpacing(5)
        self.layout.setContentsMargins(2, 2, 2, 2)

        enter = QPushButton("确定")
        self.layout.addWidget(enter)
        self.funcs = funcs
        connect(enter.clicked, self.enter)
        self.setMinimumWidth(300)
        self.setMinimumHeight(200)

        # noinspection PyArgumentList
        # self.setStyleSheet("QFormLayout {border: 1px solid white}")
        self.move(QCursor.pos())

    def enter(self):
        obj = self.obj
        obj_attrs = obj.v.attrs
        for f in self.funcs:
            kv = f()
            if kv is None:
                continue
            k, v = f()
            if v is None:
                del obj_attrs[k]
            else:
                obj_attrs[k] = v
        obj.v.name = self.register_name_input.text().strip() or obj.v.name
        obj.notify()
        self.close()


class DModifyAttr(QDialog):
    def select_color(self):
        # noinspection PyArgumentList
        color: QColor = QColorDialog.getColor(Qt.white)
        self.attr_color_input.setText(color.name())

    def __init__(self, obj: Datum[Attr], attrs: dict[str, Attr]):
        super().__init__(*empty_seq)
        self.obj = obj
        attr = obj.v
        self.attrs = attrs
        self.setWindowTitle("全局属性修改器")
        self.layout = QFormLayout()

        attr_name_input = QLineEdit(self)
        attr_color_input = QLineEdit(self)
        attr_color_picker = QPushButton("颜色")

        attr_type_label = QLabel(attr.typ)
        enter = QPushButton("确定")

        proper_sized(attr_color_picker)
        proper_sized(enter)
        proper_sized(attr_type_label)
        self.layout.addRow("属性名", attr_name_input)
        self.layout.addRow(attr_color_picker, attr_color_input)
        self.layout.addRow("类型", attr_type_label)
        self.layout.addWidget(enter)

        self.setLayout(self.layout)
        self.setFixedSize(self.sizeHint())
        self.layout.setSpacing(5)
        self.layout.setContentsMargins(2, 2, 2, 2)

        self.attr_color_input = attr_color_input
        self.attr_name_input = attr_name_input

        attr_color_input.setText(attr.color)
        attr_name_input.setText(attr.name)

        connect(attr_color_picker.clicked, self.select_color)
        connect(enter.clicked, self.enter)

        # noinspection PyArgumentList
        # self.setStyleSheet("QFormLayout {border: 1px solid white}")
        self.move(QCursor.pos())

    def enter(self):
        obj = self.obj
        attr = obj.v
        new_color = self.attr_color_input.text()
        new_name = self.attr_name_input.text()
        attr.color = new_color
        attr.name = new_name
        obj.notify()
        self.close()


class DInputAttr(QDialog):
    def select_color(self):
        # noinspection PyArgumentList
        color: QColor = QColorDialog.getColor(Qt.white)
        self.attr_color_input.setText(color.name())

    def __init__(self, ref: Datum, glob_attrs: Attrs):
        super().__init__(*empty_seq)
        self.ref = ref
        self.glob_attrs = glob_attrs
        self.setWindowTitle("属性添加器")
        self.layout = QFormLayout()

        attr_name_input = QLineEdit(self)
        attr_color_input = QLineEdit(self)
        attr_color_picker = QPushButton("颜色")

        attr_type_picker = QComboBox()
        attr_type_picker.addItem("整数")
        attr_type_picker.addItem("浮点数")
        attr_type_picker.addItem("字符串")
        enter = QPushButton("确定")

        proper_sized(attr_color_picker)
        proper_sized(enter)
        proper_sized(attr_type_picker)
        self.layout.addRow("属性名", attr_name_input)
        self.layout.addRow(attr_color_picker, attr_color_input)
        self.layout.addRow("类型", attr_type_picker)
        self.layout.addWidget(enter)

        self.setLayout(self.layout)
        self.setFixedSize(self.sizeHint())
        self.layout.setSpacing(5)
        self.layout.setContentsMargins(2, 2, 2, 2)

        self.attr_color_input = attr_color_input
        self.attr_type_picker = attr_type_picker
        self.attr_name_input = attr_name_input

        connect(attr_color_picker.clicked, self.select_color)
        connect(enter.clicked, self.enter)

        # noinspection PyArgumentList
        # self.setStyleSheet("QFormLayout {border: 1px solid white}")
        self.move(QCursor.pos())

    def enter(self):
        color = self.attr_color_input.text()
        name = self.attr_name_input.text()
        if not name.strip():
            return
        typ = self.attr_type_picker.currentText()
        new_attr = Attr(id=uuid_str(), name=name, typ=typ, color=color)
        self.ref.v = new_attr
        self.close()


class Main(QWidget):
    def __init__(self, proj_path: str):
        super().__init__(*empty_seq)
        self.setWindowTitle("纲目")

        self.proj = Project(datafile=proj_path)
        self.context = {}
        layout = self.layout = QVBoxLayout()
        self.data = Data(docs={}, attrs={}, editor="notepad")

        menu = QMenuBar()
        add_widget(layout, menu)
        outline: QMenu = menu.addMenu("大纲")
        outline.addSeparator()
        act = outline.addAction("新建大纲")
        connect(act.triggered, self.new_proj)
        act = outline.addAction("打开大纲")
        connect(act.triggered, self.open_proj)
        act = outline.addAction("打开文档")
        connect(act.triggered, self.add_nove_doc)
        act = outline.addAction("保存")
        connect(act.triggered, self.save_proj)
        act = outline.addAction("另存为")
        connect(act.triggered, self.save_proj_as)

        doc_attr: QMenu = menu.addAction("文档属性")
        connect(doc_attr.triggered, self.doc_attr)

        settings: QMenu = menu.addMenu("设置")
        connect(settings.addAction("编辑器").triggered, self.editor_setting)
        connect(settings.addAction("大纲路径").triggered, self.change_proj)

        self.layout.setSpacing(5)
        self.filter = DInput("过滤")
        self.sorter = DInput("排序", self)
        self.query_button = QPushButton("查询")
        connect(self.query_button.clicked, self.query)

        add_widget(self.layout, self.filter)
        add_widget(self.layout, self.sorter)
        add_widget(self.layout, self.query_button)
        add_widget(self.layout, separator())

        self.attrs = DocAttrs(self)
        self.attrs.layout.setAlignment(Qt.AlignCenter | Qt.AlignTop)

        self.attrs.setWindowTitle("属性编辑器")
        self.attrs.setMinimumWidth(300)
        self.attrs.setMinimumHeight(100)
        connect(self.attrs.right_click, self.attr_box_right_click)

        self.documents = DList()
        self.documents.layout.setAlignment(Qt.AlignCenter | Qt.AlignTop)

        # self.attrs.item_on_left_click = self.attrs_item_left_click
        self.attrs.item_on_right_click = self.attrs_item_right_click

        self.documents.item_on_left_click = self.document_item_left_click
        self.documents.item_on_right_click = self.document_item_right_click

        add_widget(self.layout, self.documents)
        self.setLayout(self.layout)
        self.layout.setAlignment(Qt.AlignTop | Qt.AlignCenter)

        self.load_proj(self.proj.datafile)

    def reload(self, data: Data):
        self.data = data
        self.attrs.clear()
        self.documents.clear()
        self.context = {}
        self.attr_lookup = {}
        for doc in data.docs.values():
            self.documents.add(Datum(doc))

        for attr in data.attrs.values():
            DList.add(self.attrs, Datum(attr))

    def query(self):

        def wrap(f):
            def apply(obj):
                obj = QueryProxy(obj, self.data.attrs, self.attr_lookup)
                return f(obj)
            return apply
    
        if filter_code := self.filter.register.text():
            F = eval(f"lambda _: {filter_code}")
        else:
            F = None

        if sorter_code := self.sorter.register.text():
            S = eval(f"lambda _: {sorter_code}")
        else:
            S = None

        seq = self.data.docs.values()
        try:
            if F:
                seq = list(filter(wrap(F), seq))
            else:
                seq = list(seq)
        except Exception as e:
            seq = list(self.data.docs.values())
            msg_box = QMessageBox()
            msg_box.setText(f"过滤函数有错误: {e}")
            msg_box.exec_()
        
        if S:
            try:
                seq.sort(key=wrap(S))
            except Exception as e:
                msg_box = QMessageBox()
                msg_box.setText(f"排序函数有错误: {e}")
                msg_box.exec_()

        self.documents.clear()
        for each in seq:
            self.documents.add(Datum(each))

    def editor_setting(self):
        editor_name, ok = QInputDialog.getText(self, "编辑器设置", "属性名")
        if ok:
            self.data.editor = editor_name

    def doc_attr(self):
        """
        预览大纲中的所有属性
        可删除、创建属性
        """
        self.attrs.show()

    def attr_box_right_click(self):
        popMenu = QMenu(self)
        popMenu.addAction("添加属性", self.add_attr)
        popMenu.exec_(self.cursor().pos())

    def edit_attr_for_attr(self, obj: Datum[Attr]):
        DModifyAttr(obj, self.data.attrs).exec_()

    def add_attr(self):
        datum = Datum(None)
        DInputAttr(datum, self.data.attrs).exec_()
        if datum.v is not None:
            self.attrs.add(typing.cast(Datum[Attr], datum))

    def add_nove_doc(self):
        options = QFileDialog.Options()

        init_path = str(pathlib.Path(self.proj.datafile).absolute().parent)
        # noinspection PyTypeChecker
        doc_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文档",
            init_path,
            "All Files (*)",
            options=options,
        )
        if not doc_path:
            return
        doc = Document(
            id=uuid_str(),
            name=pathlib.Path(doc_path).with_suffix("").name,
            attrs={},
            path=doc_path,
        )
        self.documents.add(Datum(doc))
        self.data.docs[doc.id] = doc

    def load_proj(self, datafile: str):
        self.proj.datafile = datafile
        data = self.proj.load()
        self.reload(data)

    def new_proj(self):
        init_path = str(pathlib.Path(self.proj.datafile).absolute().parent)
        datafile, _ = QFileDialog.getSaveFileName(
            self, "选择项目", init_path, "All Files (*);;JSON Files (*.json)"
        )

        self.proj.datafile = datafile
        self.reload(Data.empty())
        self.save_proj()

    def change_proj(self):
        init_path = str(pathlib.Path(self.proj.datafile).absolute().parent)
        datafile, _ = QFileDialog.getSaveFileName(
            self, "选择项目", init_path, "All Files (*);;JSON Files (*.json)"
        )
        self.proj.datafile = datafile

    def open_proj(self):
        options = QFileDialog.Options()
        init_path = str(pathlib.Path(self.proj.datafile).absolute().parent)
        # noinspection PyTypeChecker
        datafile, _ = QFileDialog.getOpenFileName(
            self,
            "选择项目",
            init_path,
            "All Files (*);;JSON Files (*.json)",
            options=options,
        )
        if not datafile:
            return
        self.load_proj(datafile)

    def save_proj(self):
        self.proj.save(self.data)

    def save_proj_as(self):
        options = QFileDialog.Options()
        init_path = str(pathlib.Path(self.proj.datafile).absolute().parent)
        # noinspection PyTypeChecker
        proj_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择项目",
            init_path,
            "All Files (*);;JSON Files (*.json)",
            options=options,
        )
        if not proj_path:
            return
        Project(proj_path).save(self.data)

    def ref_obj(self, datum: Datum):
        var = datum.name.isidentifier() and datum.name or ""
        while var in self.context:
            var = f"ref_{var}_{new_id()}"

        self.context[var] = datum.v
        pyperclip.copy(var)

    def edit_attr_for_doc(self, obj: Datum[Document]):
        ChangeDocAttr(obj, self.data.attrs).exec_()

    def attrs_item_right_click(self, btn: DListItem):
        popMenu = QMenu(self)
        popMenu.addAction("属性编辑", partial(self.edit_attr_for_attr, btn.datum))
        popMenu.addAction("引用", partial(self.ref_obj, btn.datum))
        popMenu.addSeparator()
        popMenu.addAction("删除", partial(self.attrs.remove, btn))
        popMenu.exec_(self.cursor().pos())

    def document_item_right_click(self, btn: DListItem):
        popMenu = QMenu(self)
        popMenu.addAction("属性编辑", partial(self.edit_attr_for_doc, btn.datum))
        popMenu.addAction("引用", partial(self.ref_obj, btn.datum))
        popMenu.addAction("在列表中删除", partial(self.documents.remove, btn))
        popMenu.addSeparator()
        popMenu.addAction("数据删除", partial(self.document_delete, btn))
        popMenu.exec_(self.cursor().pos())

    def document_delete(self, btn):
        self.documents.remove(btn)
        del self.data.docs[btn.datum.v.id]
        

    def document_item_left_click(self, btn: DListItem):
        doc: Document = typing.cast(Document, btn.datum.v)
        editor = which(self.data.editor)
        if editor is None:
            msg_box = QMessageBox()
            msg_box.setText(f"找不到编辑器: {self.data.editor}")
            msg_box.exec_()
            return

        args = [editor, doc.path]
        check_call(args)
        # self.doc_model.removeRow(i)
        # self.documents.pop()
        # self.sync_doc_strings()


sys._excepthook = sys.excepthook


def exception_hook(exctype, value, traceback):
    sys._excepthook(exctype, value, traceback)
    sys.exit(1)


sys.excepthook = exception_hook

def nove(proj_path: str):
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
    app = QApplication([])
    light(app)
    app.setAttribute(Qt.AA_EnableHighDpiScaling)
    rect = app.desktop().screenGeometry()
    win = Main(proj_path)
    area = QScrollArea()
    area.setWidget(win)

    area.setGeometry(
        100, 100, int(0.2 * rect.width()), int(0.8 * rect.height())
    )
    area.setWidgetResizable(True)
    modern = ModernWindow(area)
    modern.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    wisepy2.wise(nove)()
