"""Verify recorded clock configuration against the exact generated PS7 init.

Field definitions are in the XSA's generated ps7_init.html: ARM_CLK_CTRL SRCSEL
5:4 (0x ARM PLL), DIVISOR13:8, ARM_PLL_CTRL PLL_FDIV18:12. These are configured
frequencies, not an external oscillator measurement.
"""
from __future__ import annotations
import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ADDRESSES=(0xF8000100,0xF8000104,0xF8000108,0xF8000120)
NAMES=('ARM_PLL_CTRL','DDR_PLL_CTRL','IO_PLL_CTRL','ARM_CLK_CTRL')


def expected_configuration(xsa: Path, init: Path) -> dict:
    text=init.read_text(encoding='utf-8-sig')
    blocks=re.findall(r'proc ps7_pll_init_data_\d_\d \{\} \{(.*?)\n\}',text,re.S)
    configurations=[]
    for block in blocks:
        current={address:{'mask':0,'value':0} for address in ADDRESSES}
        for address,mask,value in re.findall(r'mask_write\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)\s+(0x[0-9a-f]+)',block,re.I):
            address,mask,value=int(address,16),int(mask,16),int(value,16)
            if address in current:
                prior=current[address]
                prior['mask']|=mask
                prior['value']=(prior['value'] & ~mask) | (value & mask)
        if any(x['mask']==0 for x in current.values()):
            raise ValueError('Missing clock register in generated PLL initialization')
        configurations.append(current)
    if not configurations or any(c!=configurations[0] for c in configurations):
        raise ValueError('Revision-specific clock initialization requires an explicit reviewed decoder')
    with zipfile.ZipFile(xsa) as archive:
        names=[x for x in archive.namelist() if x.endswith('.hwh')]
        if len(names)!=1:raise ValueError('Expected one PS hardware handoff')
        tree=ET.fromstring(archive.read(names[0]))
        values={p.attrib.get('NAME'):p.attrib.get('VALUE') for p in tree.iter('PARAMETER')}
    crystal=float(values['PCW_CRYSTAL_PERIPHERAL_FREQMHZ'])*1e6
    cpu=float(values['PCW_ACT_APU_PERIPHERAL_FREQMHZ'])*1e6
    return dict(registers={name:{**configurations[0][addr],'address':addr} for name,addr in zip(NAMES,ADDRESSES)},
        ps_input_configured_hz=crystal,xsa_cpu_configured_hz=cpu,
        provenance='ps7_init.tcl masks; same XSA .hwh frequency parameters and ps7_init.html field definitions')


def verify_configuration(data: bytes, expected: dict, status: dict) -> dict:
    if len(data)!=16:raise ValueError('Clock register snapshot must be exactly 16 bytes')
    values=dict(zip(NAMES,struct.unpack('<4I',data)))
    for name,value in values.items():
        entry=expected['registers'][name]
        if value & entry['mask'] != entry['value']:
            raise ValueError('Runtime clock differs from same-XSA PS init: '+name)
    pll,clock=values['ARM_PLL_CTRL'],values['ARM_CLK_CTRL']
    source=(clock>>4)&3;divisor=(clock>>8)&0x3F;feedback=(pll>>12)&0x7F
    if source not in (0,1) or divisor==0 or feedback==0 or pll & 0x13:
        raise ValueError('Unsupported, reset, powered-down or bypassed ARM clock configuration')
    derived=expected['ps_input_configured_hz']*feedback/divisor
    nominal=status['cpu_hz']
    if nominal<=0 or abs(derived-nominal)>nominal*1e-5 or abs(expected['xsa_cpu_configured_hz']-nominal)>nominal*1e-5:
        raise ValueError('Decoded clock does not corroborate BSP/XSA nominal CPU frequency')
    if status['timer_hz']!=nominal//2:
        raise ValueError('BSP timer must correspond to CPU/2')
    return dict(raw_registers=values,matched_generated_masks=True,source='ARM PLL',feedback_divisor=feedback,
        cpu_divisor=divisor,ps_input_configured_hz=expected['ps_input_configured_hz'],
        cpu_from_registers_and_configured_input_hz=derived,cpu_bsp_nominal_hz=nominal,
        timer_bsp_nominal_hz=status['timer_hz'],interpretation='Configuration corroboration only; physical oscillator frequency was not measured')
