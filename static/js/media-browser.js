// Media source selector. Folders are stored as folders; expansion belongs to
// manifest generation so a later file added to a folder is picked up too.
(function () {
 "use strict";
 function init(root) {
  var labels=JSON.parse(document.getElementById("media-tree-labels").textContent), listUrl=root.dataset.listUrl;
  var selection={folders:(labels.initialFolders||[]).slice(),files:(labels.initialFiles||[]).slice()};
  var out=document.getElementById("selected-files"), empty=document.getElementById("selected-files-empty"), hidden=document.getElementById("selected-paths-json"), mode=document.getElementById("play_mode");
  function single(){return (labels.singleFileModes||[]).map(String).indexOf(mode.value)!==-1}
  function join(base,name){return base?base+"/"+name:name}
  function selected(path,type){return selection[type].indexOf(path)!==-1}
  function save(){hidden.value=JSON.stringify(selection); render(); sync()}
  function remove(path,type){var i=selection[type].indexOf(path);if(i>=0)selection[type].splice(i,1);save()}
  function add(path,type){if(type==="folders"&&single()){window.alert(labels.singleFileOnly);return} if(single()){selection={folders:[],files:[path]}}else if(!selected(path,type))selection[type].push(path);save()}
  function render(){out.innerHTML="";var all=selection.folders.map(function(p){return [p,"folders","📁 "]}).concat(selection.files.map(function(p){return [p,"files","🎵 "]}));empty.hidden=all.length>0;all.forEach(function(row){var li=document.createElement("li"), text=document.createElement("span"), b=document.createElement("button");text.textContent=row[2]+row[0];b.type="button";b.className="mt-remove";b.textContent="×";b.title=labels.remove;b.onclick=function(){remove(row[0],row[1])};li.appendChild(text);li.appendChild(b);out.appendChild(li)})}
  function sync(){root.querySelectorAll(".mt-file input").forEach(function(x){x.checked=selected(x.closest("li").dataset.path,"files")});root.querySelectorAll(".mt-use-btn").forEach(function(b){var active=selected(b.closest("li").dataset.path,"folders");b.textContent=active?labels.unuseFolder:labels.useFolder;b.disabled=single()&&!active})}
  function fetchList(path){return fetch(listUrl+"?path="+encodeURIComponent(path)).then(function(r){if(!r.ok)throw Error();return r.json()})}
  function children(parent,data){parent.innerHTML="";data.dirs.forEach(function(name){parent.appendChild(dir(join(data.path,name),name))});data.files.forEach(function(name){parent.appendChild(file(join(data.path,name),name))});if(!data.dirs.length&&!data.files.length){var x=document.createElement("li");x.className="mt-empty muted";x.textContent=labels.emptyFolder;parent.appendChild(x)}}
  function dir(path,name){var li=document.createElement("li"),row=document.createElement("div"),toggle=document.createElement("span"),icon=document.createElement("span"),title=document.createElement("span"),button=document.createElement("button"),child=document.createElement("ul");li.className="mt-dir";li.dataset.path=path;row.className="mt-row";toggle.className="mt-toggle";toggle.textContent="▷";icon.className="mt-icon";icon.textContent="📁";title.className="mt-name";title.textContent=name;button.type="button";button.className="mt-use-btn";child.className="mt-children";child.hidden=true;function expand(){if(li.dataset.loaded){child.hidden=!child.hidden;toggle.textContent=child.hidden?"▷":"▾";return}toggle.textContent="…";fetchList(path).then(function(data){children(child,data);li.dataset.loaded="1";child.hidden=false;toggle.textContent="▾"}).catch(function(){toggle.textContent="▷";window.alert(labels.listError)})}toggle.onclick=expand;title.onclick=expand;button.onclick=function(){selected(path,"folders")?remove(path,"folders"):add(path,"folders")};row.append(toggle,icon,title,button);li.append(row,child);return li}
  function file(path,name){var li=document.createElement("li"),box=document.createElement("input"),icon=document.createElement("span"),title=document.createElement("span");li.className="mt-file";li.dataset.path=path;box.type="checkbox";box.checked=selected(path,"files");box.onchange=function(){box.checked?add(path,"files"):remove(path,"files")};icon.className="mt-icon";icon.textContent="🎵";title.className="mt-name";title.textContent=name;li.append(box,icon,title);return li}
  var tree=document.createElement("ul");tree.className="mt-root";root.appendChild(tree);fetchList("").then(function(data){children(tree,data);sync()}).catch(function(){tree.textContent=labels.listError});mode.onchange=function(){if(single()&&(selection.folders.length||selection.files.length>1)){selection={folders:[],files:selection.files.slice(0,1)};save()}sync()};save()
 }
 document.addEventListener("DOMContentLoaded",function(){var r=document.getElementById("media-tree");if(r)init(r)})
})();
