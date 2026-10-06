"""Negative fixtures for rejecting unsafe/wrong PS, DMA and IRQ configurations."""
import copy
from pathlib import Path
import unittest
import audit_configuration as a


class ConfigurationGate(unittest.TestCase):
    def setUp(self):
        self.original = a.properties(a.BASELINE/'reports/ps7_parameters.tsv')
        self.ps = dict(self.original)
        self.ps.update({k: str(v) for k, v in a.PS_EXPECTED.items()})
        self.ps.update({'CONFIG.Component_Name': 'zybo_dma_processing_system7_0_0',
                        'CONFIG.PCW_FCLK0_PERIPHERAL_DIVISOR0': '5',
                        'CONFIG.PCW_FCLK0_PERIPHERAL_DIVISOR1': '2',
                        'CUSTOMIZATION_CRC': '0123abcd'})
        self.dma = {k: str(v) for k, v in a.DMA_EXPECTED.items()}
        self.dma.update({'VLNV': 'xilinx.com:ip:axi_dma:7.1',
                         'CONFIG.Component_Name': 'zybo_dma_axi_dma_0_0',
                         'CONFIG.c_micro_dma': '0', 'CONFIG.c_prmry_is_aclk_async': '0'})
        self.addresses = [dict(address_space='/processing_system7_0/Data',
                               segment='SEG_'+name+'_Reg', offset=base, range='0x10000')
                          for name, base in [('mfcc_dma_0', '0x43c00000'), ('axi_dma_0', '0x40400000')]]
        self.addresses += [dict(address_space='/axi_dma_0/'+name,
                                segment='SEG_processing_system7_0_HP0_DDR_LOWOCM',
                                offset='0x0', range='0x20000000') for name in ('Data_MM2S', 'Data_S2MM')]
        self.irqs = [dict(net=str(i), pins=' '.join(pins)) for i, pins in enumerate([
            ('/axi_dma_0/mm2s_introut', '/irq_concat/In0'),
            ('/axi_dma_0/s2mm_introut', '/irq_concat/In1'),
            ('/mfcc_dma_0/irq', '/irq_concat/In2'),
            ('/irq_concat/dout', '/processing_system7_0/IRQ_F2P')])]

    def test_nominal_fixture(self):
        self.assertEqual(a.compare_ps(self.original, self.ps)['status'], 'PASS')
        self.assertEqual(a.check_dma(self.dma)['status'], 'PASS')
        a.check_addresses(self.addresses)
        a.check_irqs(self.irqs)

    def test_frozen_ps_mutations_rejected(self):
        protected = [next(k for k in self.ps if k.startswith('CONFIG.') and token in k and k not in a.PS_ALLOWED)
                     for token in ('DDR', 'MIO', 'CPU', 'PLL')]
        changes = {k: 'UNEXPECTED' for k in protected}
        changes.update({'CONFIG.PCW_USE_S_AXI_HP0': '0', 'CONFIG.PCW_IRQ_F2P_INTR': '0',
                        'CONFIG.PCW_NUM_F2P_INTR_INPUTS': '1',
                        'CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ': '99',
                        'CONFIG.PCW_FCLK0_PERIPHERAL_DIVISOR1': '3',
                        'CONFIG.Component_Name': 'wrong_core', 'CONFIG.UNEXPECTED': '1'})
        for key, value in changes.items():
            with self.subTest(key=key):
                changed = dict(self.ps, **{key: value})
                self.assertEqual(a.compare_ps(self.original, changed)['status'], 'FAIL')
        del self.ps['CONFIG.PCW_USE_S_AXI_HP0']
        self.assertEqual(a.compare_ps(self.original, self.ps)['status'], 'FAIL')

    def test_each_dma_contract_property_rejected_if_changed(self):
        for key in self.dma:
            with self.subTest(key=key):
                changed = dict(self.dma, **{key: 'UNEXPECTED'})
                self.assertEqual(a.check_dma(changed)['status'], 'FAIL')
        for key in a.DMA_EXPECTED:
            with self.subTest(missing=key):
                changed = dict(self.dma)
                del changed[key]
                self.assertEqual(a.check_dma(changed)['status'], 'FAIL')

    def test_address_alias_or_truncation_rejected(self):
        for row in range(4):
            for field in ('offset', 'range'):
                with self.subTest(row=row, field=field):
                    changed = copy.deepcopy(self.addresses)
                    changed[row][field] = '0x1234'
                    with self.assertRaises(ValueError):
                        a.check_addresses(changed)
        with self.assertRaises(ValueError):
            a.check_addresses(self.addresses+self.addresses[2:3])
        with self.assertRaises(ValueError):
            a.check_addresses(self.addresses[:-1])

    def test_irq_swaps_disconnects_and_aliases_rejected(self):
        for i in range(4):
            changed = copy.deepcopy(self.irqs)
            changed[i]['pins'] = changed[i]['pins'].split()[0]
            with self.assertRaises(ValueError):
                a.check_irqs(changed)
        changed = copy.deepcopy(self.irqs)
        changed[0]['pins'] = changed[0]['pins'].replace('In0', 'In1')
        changed[1]['pins'] = changed[1]['pins'].replace('In1', 'In0')
        with self.assertRaises(ValueError):
            a.check_irqs(changed)

    def test_slave_apertures_do_not_become_address_mappings(self):
        definitions = [dict(address_space='/'+space, segment='/'+space+'/'+leaf,
                            offset='', range=size) for space, leaf, size in (
            ('axi_dma_0/S_AXI_LITE', 'Reg', '0x1000'),
            ('mfcc_dma_0/S_AXI', 'reg0', '0x10000'),
            ('processing_system7_0/S_AXI_HP0', 'HP0_DDR_LOWOCM', '0x40000000'))]
        self.assertEqual(a.check_addresses(self.addresses+definitions),
                         a.check_addresses(self.addresses))
        for row in range(3):
            for field, value in (('address_space', '/unknown'),
                                 ('segment', '/unknown'), ('range', '0x1234')):
                changed = copy.deepcopy(definitions)
                changed[row][field] = value
                with self.subTest(row=row, field=field), self.assertRaises(ValueError):
                    a.check_addresses(self.addresses+changed)
        with self.assertRaises(ValueError):
            a.check_addresses(self.addresses+definitions+definitions[:1])
        changed = copy.deepcopy(self.addresses)
        changed[0]['offset'] = ''
        with self.assertRaises(ValueError):
            a.check_addresses(changed+definitions)
        with self.assertRaises(ValueError):
            a.check_addresses(self.addresses+[
                dict(address_space='/unknown', segment='/unknown', offset='0', range='0x1000')])
        changed = copy.deepcopy(self.irqs)
        changed[1]['net'] = changed[0]['net']
        with self.assertRaises(ValueError):
            a.check_irqs(changed)


if __name__ == '__main__':
    unittest.main()
