import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import os
import json

import chardet
def extract_text_by_bookmark(book, start_href, end_href = ""):
    """
    end_href: 搜索截止处。若为空字符串则直接搜索到结尾
    """
    # 查找 href 对应的章节
    start_name = start_href.split("#")[0]
    end_name = end_href.split("#")[0]
    start_anchor = start_href.split("#")[1] if "#" in start_href else ""
    end_anchor = end_href.split("#")[1] if "#" in end_href else ""
    
    
    if start_name == end_name:
        for item in book.get_items():
            if start_name in item.file_name:
                result = chardet.detect(item.get_content())
                encoding = result['encoding'] if result else 'utf-8'
                soup = BeautifulSoup(item.get_content(), 'html.parser',from_encoding=encoding)
                return extract_text_between_anchors(soup,start_anchor,end_anchor)
        print("href not found.")
        return ""   
    flag = False 
    text = ""
    for item in book.get_items():
        if start_name in item.file_name:  # 匹配 href 文件名
            flag = True
            # 解析 HTML 内容
            result = chardet.detect(item.get_content())
            encoding = result['encoding'] if result else 'utf-8'
            soup = BeautifulSoup(item.get_content(), 'html.parser',from_encoding = encoding)
            # 如果是锚点 (#)，提取对应部分
            if start_anchor:
                text += extract_text_after_anchor(soup,start_anchor)
            else:
                text += soup.get_text()
        elif end_name in item.file_name:
            flag = False
            if end_anchor:
                result = chardet.detect(item.get_content())
                encoding = result['encoding'] if result else 'utf-8'
                soup = BeautifulSoup(item.get_content(), 'html.parser',from_encoding = encoding)
                text += extract_text_before_anchor(soup,end_anchor)
                
        elif flag:
            result = chardet.detect(item.get_content())
            encoding = result['encoding'] if result else 'utf-8'
            soup = BeautifulSoup(item.get_content(), 'html.parser',from_encoding = encoding)
            text += soup.get_text()
    return text
            
def extract_text_after_anchor(soup, anchor):
    # 找到具有指定 ID 的元素
    element = soup.find(id=anchor)
    if not element:
        print("Anchor not found in the document.")
        return ""
    
    result = []
    # 添加目标元素的文本
    if element.get_text():
        result.append(element.get_text())
    
    # 遍历后续兄弟节点
    for sibling in element.next_siblings:
        if sibling.name:  # 如果是标签节点，提取其文本
            result.append(sibling.get_text())
        elif isinstance(sibling, str):  # 如果是字符串，直接添加
            result.append(sibling.strip())
    
    return "\n".join(result)

def extract_text_before_anchor(soup, anchor):
    # 找到具有指定 ID 的元素
    element = soup.find(id=anchor)
    if not element:
        print("Anchor not found in the document.")
        return ""
    
    result = []
    
    # 遍历目标元素之前的兄弟节点
    for sibling in element.previous_siblings:
        if sibling.name:  # 如果是标签节点，提取其文本
            result.insert(0, sibling.get_text())  # 插入到前面，保持顺序
        elif isinstance(sibling, str):  # 如果是字符串，直接添加
            result.insert(0, sibling.strip())
    
    return "\n".join(result).strip()

def extract_text_between_anchors(soup, start_anchor, end_anchor):
    # 找到 start_anchor 和 end_anchor 元素
    start_element = soup.find(id=start_anchor)
    end_element = soup.find(id=end_anchor)
    
    if not start_element:
        return extract_text_before_anchor(soup,end_anchor)
    if not end_element:
        return extract_text_after_anchor(soup,start_anchor)
    
    result = []
    
    # 添加 start_anchor 的文本
    if start_element.get_text():
        result.append(start_element.get_text())
    
    # 遍历从 start_anchor 到 end_anchor 的兄弟节点
    for sibling in start_element.next_siblings:
        # 停止在 end_anchor
        if sibling == end_element:
            break
        # 如果是标签节点，提取文本
        if sibling.name:
            result.append(sibling.get_text())
        # 如果是字符串节点，直接添加
        elif isinstance(sibling, str):
            result.append(sibling.strip())
    
    return "\n".join(result).strip()

def extract_bookmarks(toc, prefix = ""):
    bookmarks = []
    titles = []
    for item in toc:
        if isinstance(item, epub.Link):
            if prefix:
                title = prefix+"|"+item.title
            else:
                title = item.title
            titles.append(title)
            if title in titles[:-1]:
                title += str(titles.count(title) + 1)
            bookmarks.append((title, item.href))
            
        elif isinstance(item, tuple):
            bookmarks.extend(extract_bookmarks(item[1],item[0].title))
    return bookmarks

def split_text_by_bookmarks(book):
    chapters = []
    bookmarks = extract_bookmarks(book.toc)
    n = len(bookmarks)
    for i,(title,href) in enumerate(bookmarks):
        end_href = bookmarks[i+1][1] if i < n-1 else ""
        chapters.append({
            "title":title,
            "content":extract_text_by_bookmark(book,href,end_href)
        })
        print(title)
    return chapters

def save_json_file(path,target):
    dir_name = os.path.dirname(path)
    if not os.path.exists(dir_name):
        os.makedirs(dir_name)
    with open(path,"w",encoding="utf-8") as f:
        json.dump(target, f, ensure_ascii=False,indent=True)
        
if __name__ == "__main__":
    title = "祈祷之海"
    book = epub.read_epub(f'./{title}.epub')
    save_json_file(f'./{title}.json',split_text_by_bookmarks(book))