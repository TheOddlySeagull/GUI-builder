function loadJson(filePath) {
    var fileReader = new java.io.FileReader(filePath);
    var bufferedReader = new java.io.BufferedReader(fileReader);
    var content = "";
    var line;
    while ((line = bufferedReader.readLine()) !== null) {
        content += line;
    }
    bufferedReader.close();
    if (content.trim() === "") {
        return null;
    }
    return JSON.parse(content);
}

function findJsonEntry(json, key, value) {
    for (var i = 0; i < json.length; i++) {
        if (json[i][key] === value) {
            return json[i];
        }
    }

    return null;
}

function tellPlayer(player, message) {
    player.message('§a[GUI Builder] §f' + message);
}

var TILE_SCALE = 16;
var ITEM_OFFSET_X = -2.5;
var ITEM_OFFSET_Y = -2.8;

var _currentPageID = null;
var _manifest = null;
var _skinPack = null;

function guiBuilder_textureBase() {
    return 'minecraft:textures/gui/gui_creator/' + _manifest.gui_name + '/' + _skinPack + '/';
}

function guiBuilder_backgroundTexture() {
    return guiBuilder_textureBase() + 'background_page_' + _currentPageID + '.png';
}

function guiBuilder_sheetTexture(sheetId) {
    return guiBuilder_textureBase() + 'sheet_' + sheetId + '.png';
}

function guiBuilder_computeSizePx(tileW, tileH) {
    
    tileW = tileW * TILE_SCALE;
    tileH = tileH * TILE_SCALE;

    return { w: tileW, h: tileH };
}

function guiBuilder_getPagesID(manifest) {
    var pages = [];
    for (var i = 0; i < manifest.pages.length; i++) {
        pages.push(manifest.pages[i].page);
    }
    return pages;
}

function guiBuilder_getAllSheets(manifest) {
    var sheets = [];
    for (var i = 0; i < manifest.pages.length; i++) {
        for (var j = 0; j < manifest.pages[i].components.length; j++) {
            var sheet = manifest.pages[i].components[j].sheet;
            if (sheets.indexOf(sheet) === -1) {
                sheets.push(sheet);
            }
        }
    }
    return sheets;
}

function guiBuilder_getAllIDs(manifest) {
    var ids = [];
    var manifest_page = findJsonEntry(manifest.pages, 'page', _currentPageID);
    for (var i = 0; i < manifest_page.components.length; i++) {
        ids.push(manifest_page.components[i].id);
    }
    return ids;
}

function guiBuilder_getAllButtonIDs() {
    var buttonIDs = [];
    var manifest_page = findJsonEntry(_manifest.pages, 'page', _currentPageID);
    for (var i = 0; i < manifest_page.components.length; i++) {
        switch (manifest_page.components[i].type) {
            case 'button':
                buttonIDs.push(manifest_page.components[i].id);
                break;
            case 'toggle_button':
                buttonIDs.push(manifest_page.components[i].id);
                break;
        }
    }
    return buttonIDs;
}

function guiBuilder_countElements() {
    var sheets = [];
    var pages = [];
    for (var i = 0; i < _manifest.pages.length; i++) {
        pages.push(_manifest.pages[i].page);
        for (var j = 0; j < _manifest.pages[i].components.length; j++) {
            if (sheets.indexOf(_manifest.pages[i].components[j].sheet) === -1) {
                sheets.push(_manifest.pages[i].components[j].sheet);
            }
        }
    }
    return {
        sheets_count: sheets.length,
        pages_count: pages.length,
        sheets: sheets,
        pages: pages 
    }
}

function guiBuilder_buildTextField(GUI, component) {
    var id = component.id;
    var posX = component.offset.x * TILE_SCALE;
    var posY = component.offset.y * TILE_SCALE;
    var sizeW = component.size_tiles.w * TILE_SCALE;
    var sizeH = component.size_tiles.h * TILE_SCALE;

    GUI.addTextField(id, posX, posY, sizeW, sizeH);
    guiBuilder_buildDefault(GUI, id, component);
    guiBuilder_buildMeta(GUI, id, component);

}

function guiBuilder_buildScrollList(GUI, component) {
    var id = component.id;
    var posX = component.offset.x * TILE_SCALE;
    var posY = component.offset.y * TILE_SCALE;
    var sizeW = component.size_tiles.w * TILE_SCALE;
    var sizeH = component.size_tiles.h * TILE_SCALE;
    var items = component.items || [];

    GUI.addScroll(id, posX, posY, sizeW, sizeH, items);
    guiBuilder_buildMeta(GUI, id, component);
}

function guiBuilder_buildDisabledButton(GUI, component) {
    var toggled = !!component.toggled;
    var locked = !!component.locked;

    var textureX = component.tex.x;
    var textureY = component.tex.y;

    if (locked) {
        if (toggled) {
            textureX = component.toggle_disabled_tex.x;
            textureY = component.toggle_disabled_tex.y;
        } else {
            textureX = component.disabled_tex.x;
            textureY = component.disabled_tex.y;
        }
    } else if (toggled) {
        textureX = component.toggle_tex.x;
        textureY = component.toggle_tex.y;
    }
    var id = component.id;
    var posX = component.offset.x * TILE_SCALE;
    var posY = component.offset.y * TILE_SCALE;
    var sizeW = component.size_tiles.w * TILE_SCALE;
    var sizeH = component.size_tiles.h * TILE_SCALE;
    var sheetTexture = guiBuilder_sheetTexture(component.sheet);

    GUI.addTexturedRect(id, sheetTexture, posX, posY, sizeW, sizeH, textureX, textureY);
    guiBuilder_buildMeta(GUI, id, component);

}

function guiBuilder_buildButton(GUI, component) {
    if (component.locked) {
        guiBuilder_buildDisabledButton(GUI, component);
        return;
    }
    var id = component.id;
    var posX = component.offset.x * TILE_SCALE;
    var posY = component.offset.y * TILE_SCALE;
    var sizeW = component.size_tiles.w * TILE_SCALE;
    var sizeH = component.size_tiles.h * TILE_SCALE;
    var textureX = component.tex.x;
    var textureY = component.tex.y;
    var label = component.label || '';
    var sheetTexture = guiBuilder_sheetTexture(component.sheet);

    GUI.addTexturedButton(id, label, posX, posY, sizeW, sizeH, sheetTexture, textureX, textureY);
    guiBuilder_buildMeta(GUI, id, component);

}

function guiBuilder_updateToggleButton(GUI, component, player) {
    GUI.removeComponent(component.id);
    guiBuilder_buildToggleButton(GUI, component);
    GUI.update(player);
}

function guiBuilder_buildToggleButton(GUI, component) {
    if (component.locked) {
        guiBuilder_buildDisabledButton(GUI, component);
        return;
    }
    var toggled = !!component.toggled;
    var disabled = !!component.disabled;

    var textureX = component.tex.x;
    var textureY = component.tex.y;

    if (disabled && component.disabled_tex) {
        textureX = component.disabled_tex.x;
        textureY = component.disabled_tex.y;
    } else if (toggled && component.toggle_tex) {
        textureX = component.toggle_tex.x;
        textureY = component.toggle_tex.y;
    }
    var id = component.id;
    var posX = component.offset.x * TILE_SCALE;
    var posY = component.offset.y * TILE_SCALE;
    var sizeW = component.size_tiles.w * TILE_SCALE;
    var sizeH = component.size_tiles.h * TILE_SCALE;
    var label = component.label || '';
    var sheetTexture = guiBuilder_sheetTexture(component.sheet);

    GUI.addTexturedButton(id, label, posX, posY, sizeW, sizeH, sheetTexture, textureX, textureY);
    guiBuilder_buildMeta(GUI, id, component);
}

function guiBuilder_buildLabel(GUI, component) {
    var id = component.id;
    var label = component.label || '';
    var posX = component.offset.x * TILE_SCALE;
    var posY = component.offset.y * TILE_SCALE;
    var sizeW = component.size_tiles.w * TILE_SCALE;
    var sizeH = component.size_tiles.h * TILE_SCALE;

    posX = posX + (TILE_SCALE / 2);
    sizeW = sizeW - TILE_SCALE;
    posY = posY + (TILE_SCALE / 2);
    sizeH = sizeH - TILE_SCALE;

    GUI.addLabel(id, label, posX, posY, sizeW, sizeH);
    guiBuilder_buildMeta(GUI, id, component);
}

function guiBuilder_buildItemSlot(GUI, component) {
    var rawX = ((component.size_tiles.w - 1)/2) + component.offset.x
    var rawY = ((component.size_tiles.h - 1)/2) + component.offset.y
    var posX = (rawX + ITEM_OFFSET_X) * TILE_SCALE;
    var posY = (rawY + ITEM_OFFSET_Y) * TILE_SCALE;
    var slot = GUI.addItemSlot(posX, posY);
    // Item slots are not addressable via GUI.getComponent(id), so apply hover text directly if supported.
    if (component.hover_text && slot && typeof slot.setHoverText === 'function') {
        slot.setHoverText(component.hover_text);
    }
}

function guiBuilder_buildDefault(GUI, componentID, component) {
    if (component.label == '' || typeof component.label === 'undefined') {
        return;
    }

    if (component.type === 'text_field') {
        var guiComponent = GUI.getComponent(componentID);
        guiComponent.setText(component.label);
    }
}

function guiBuilder_buildMeta(GUI, componentID, component) {
    if (!component.hover_text) {
        return;
    }

    var guiComponent = GUI.getComponent(componentID);
    if (guiComponent && typeof guiComponent.setHoverText === 'function') {
        guiComponent.setHoverText(component.hover_text);
    }
}

function guiBuilder_OpenPage(player, GUI, NewpageID, api) {
    var allIDs = guiBuilder_getAllIDs(_manifest);
    for (var i = 0; i < allIDs.length; i++) {
        GUI.removeComponent(allIDs[i]);
    }

    _currentPageID = NewpageID;

    GUI = guiBuilder_assembleGUI(GUI, player);

    GUI.update(player);
}

function customGuiButton(event) {
    var b1 = event.buttonId;

    var buttonIDs = guiBuilder_getAllButtonIDs(findJsonEntry(_manifest.pages, 'page', _currentPageID));

    for (var i = 0; i < buttonIDs.length; i++) {
        if (b1 === buttonIDs[i]) {
            tellPlayer(event.player, 'Button pressed: ' + b1);
        }
    }

    var buttonManifest = findJsonEntry(findJsonEntry(_manifest.pages, 'page', _currentPageID).components, 'id', b1);
    
    if (buttonManifest.hasOwnProperty('open_page')) {
        var newPageID = buttonManifest.open_page;;
        tellPlayer(event.player, 'Opening page: ' + newPageID);
        guiBuilder_OpenPage(event.player, event.gui, newPageID, event.API);
    } else if (buttonManifest.hasOwnProperty('close_gui')) {
        event.player.closeGui();
    } else if (buttonManifest.type === 'toggle_button') {
        if (buttonManifest.disabled) {
            tellPlayer(event.player, 'Toggle is disabled: ' + b1);
            return;
        }
        tellPlayer(event.player, 'Toggling button: ' + b1 + ' from ' + buttonManifest.toggled + ' to ' + !buttonManifest.toggled);
        buttonManifest.toggled = !buttonManifest.toggled;
        guiBuilder_updateToggleButton(event.gui, buttonManifest, event.player);
    }

    switch (b1) {
        // case 'some_button_id':
        //     // Do something
        //     break;
    }
}

function customGuiScroll(event) {
    var scrollSelection = event.selection;
    tellPlayer(event.player, 'Scrolled to selection: ' + scrollSelection[0]);
}

function guiBuilder_assembleGUI(GUI, player) {
    var bgTexture = guiBuilder_backgroundTexture();

    tellPlayer(player, 'Using background texture: ' + bgTexture);

    GUI.setBackgroundTexture(bgTexture);
    var manifest_page = findJsonEntry(_manifest.pages, 'page', _currentPageID);

    tellPlayer(player, 'Building GUI for page ' + _currentPageID + ' with ' + manifest_page.components.length + ' components.');

    for (var i = 0; i < manifest_page.components.length; i++) {
        var component = manifest_page.components[i];
        if (component.type === 'button') {
            guiBuilder_buildButton(GUI, component);
        } else if (component.type === 'toggle_button') {
            guiBuilder_buildToggleButton(GUI, component);
        } else if (component.type === 'label') {
            guiBuilder_buildLabel(GUI, component);
        } else if (component.type === 'item_slot') {
            guiBuilder_buildItemSlot(GUI, component);
        } else if (component.type === 'text_field') {
            guiBuilder_buildTextField(GUI, component);
        } else if (component.type === 'scroll_list') {
            guiBuilder_buildScrollList(GUI, component);
        }
    }
    return GUI;
}

function guiBuilder_buildGuiFromManifest(api, manifest, skinPack, pageID, player) {

    _currentPageID = pageID;
    _manifest = manifest;
    _skinPack = skinPack;

    var GUI = api.createCustomGui(pageID, manifest.size * TILE_SCALE, manifest.size * TILE_SCALE, false);
    GUI = guiBuilder_assembleGUI(GUI, player);

    player.showCustomGui(GUI);
}

function guiBuilder_pickSkinPack(manifest, preferred) {
    if (!manifest || !manifest.skin_packs || manifest.skin_packs.length === 0) {
        return preferred || null;
    }
    if (preferred && manifest.skin_packs.indexOf(preferred) !== -1) {
        return preferred;
    }
    return manifest.skin_packs[0];
}

var MANIFEST_PATH = 'GUI_builder/gui_manifest.json';

function init(event) {
    var item = event.item;
    item.setDurabilityShow(false);
    item.setCustomName('§6§lGUI Builder Debug Tool');
    return true;
}

function interact(event) {
    var g_manifest = loadJson(MANIFEST_PATH);
    var g_skinPack = guiBuilder_pickSkinPack(g_manifest, null);

    tellPlayer(event.player, 'Using skin pack: ' + g_skinPack);

    var pageID = guiBuilder_getPagesID(g_manifest)[0];

    tellPlayer(event.player, 'Loaded GUI manifest: ' + MANIFEST_PATH + ' at page ' + pageID);

    guiBuilder_buildGuiFromManifest(event.API, g_manifest, g_skinPack, pageID, event.player);
}