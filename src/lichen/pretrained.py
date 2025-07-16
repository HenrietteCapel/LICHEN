"""Run inference on LICHEN """
import torch
import random
import pandas as pd
import math
import time

from .load_model import load_model, configure_cpus
from .utils import passing_anarcii_filtering, passing_humatch, AbLang2_confidence, diversity_AbLang2, MAP_TYPE_SEED, MAP_GENE_FAM_SEED, MAP_GENE_SEED

class LICHEN():
    """Initialise LICHEN"""

    def __init__(self, path_to_model, device='cpu', ncpu=-1):
        super().__init__()
        
        self.used_device = torch.device(device)
        self.ncpu = configure_cpus(ncpu)
        self.LICHEN = load_model(path_to_model, self.used_device)

    def light_generation(self, input:str|list, germline_seed:list=[None], custom_seed:str=None, cdrs:list=[None, None, None], numbering_scheme:str='IMGT',n:int=1, filtering:list=None):
        """Generate light sequences for the input heavy sequence
        
        Parameters
        ----------
        input : str|list
            The heavy sequence or heavy sequences (in case of a bispecific) for one a light
            sequeces needs to be generated.
        germline_seed : list
            Type, V-gene family, or V-genes to use.
        custom_seed : str
            Custom seed to use.
        cdrs : list
            Containing the CDRL1, CDRL2, and CDRL3 for grafting.
            When CDR grafting requested, 
        numbering_scheme : str:
            Either IMGT or Kabat. Used for CDR definition when CDR grafting.
        n : int
            Number of light sequences requested per heavy sequence.
        filtering : list
            List of filtering steps to perform, if empty no filtering is applied.
            Options are: 'redundancy', 'diversity', 'ANARCII', 'Humatch', 'AbLang2',
            and combinations thereof (except 'diversity' and 'AbLang2', which defaults to 'diversity')
            When filtering requested 10 times more sequences will be generated than
            requested to apply filtering on. 
        """
        # Check input formats
        if isinstance(input, str):
            input = [input]
        for heavy_seq in input:
            if len(heavy_seq)>0 and len(heavy_seq)<80:
                raise SyntaxError("Incomplete heavy sequence provided")
        if not isinstance(germline_seed, list):
            raise SyntaxError("'germline_seed' needs to be provided as a list")
        if not isinstance(cdrs, list):
            raise SyntaxError("'cdrs' needs to be provided as a list")
        if not len(cdrs) == 3:
            raise SyntaxError("When providing CDRs all three CDRs need to be given, use None if not all CDRs need to be fixed i.e. [None, 'DAS', None]")
        if not numbering_scheme in ['IMGT', 'Kabat']:
            raise SyntaxError(f"Incorrect numbering scheme use 'IMGT' or 'Kabat.")
        if filtering:
            if not isinstance(filtering, list):
                raise SyntaxError("'filtering' needs to be provided as a list")
            for tool in filtering: 
                if not tool in ['redundancy', 'diversity', 'ANARCII', 'Humatch', 'AbLang2']:
                    raise SyntaxError(f"Given filtering {tool} doesn't exist.")
        
        if any(germline_seed) and custom_seed:
            print('Cannot provide germline seeds and custom seed, custom seed will be used')
        
        # Check number of repeats required
        if filtering or any(cdrs):
            repeats = n*10
        else:
            repeats = n

        # Handle seed
        if custom_seed:
            light_seeds = [custom_seed]
        elif any(germline_seed):
            light_seeds = []
            for germline in germline_seed:
                light_seeds.extend(self.get_possible_seeds(germline))
        else:
            light_seeds=None

        # Handle CDRs
        if any(cdrs):
            light_cdr = cdrs
        else:
            light_cdr = None
        

        # Generate the light sequences
        light_sequences = []
        for rep in range(repeats):
            # Sample a seed from possible seeds (if given)
            if light_seeds:
                light_seed = random.sample(light_seeds, k=1)[0]
            else:
                light_seed = None

            gen_light = self.LICHEN.generate_light(input, light_seed, light_cdr, numbering_scheme)

            if filtering and 'ANARCII' in filtering:
                if not passing_anarcii_filtering(gen_light, light_cdr, numbering_scheme):
                    continue 
            if filtering and 'Humatch' in filtering:
                if not passing_humatch(gen_light):
                    continue

            light_sequences.append(gen_light)
    
        # remove duplicates
        if filtering and 'redundancy' in filtering:
            light_sequences = list(set(light_sequences))
        if len(light_sequences) < n:
            print(f'Only {len(light_sequences)} sequences could be generated that pass all requested filtering')
            return light_sequences
        elif filtering and 'diversity' in filtering:
            return diversity_AbLang2(light_sequences, n) 
        elif filtering and 'AbLang2' in filtering:
            return AbLang2_confidence(light_sequences, n) 
        else:
            return random.sample(light_sequences, k=n)
                

    def light_generation_bulk(self, input, numbering_scheme:str='IMGT', n:int=1):
        """
        Generates light sequences for an input DataFrame

        Parameters
        ----------
        input : DateFrame
            DataFrame containing multiple sequences and potentially additional information.
        n : int
            Number of light sequences requested per heavy sequence.
        """
        if not 'heavy' in input.columns:
            raise SyntaxError("The input dataframe should contain a column named 'heavy' with the heavy sequence")
        if not 'germline_seed' in input.columns:
            input['germline_seed'] = [[None]]*len(input)
        if not 'custom_seed' in input.columns:
            input['custom_seed'] = [None]*len(input)
        if not 'cdrs' in input.columns:
            input['cdrs'] = [[None, None, None]]*len(input)
        if not 'filtering' in input.columns:
            input['filtering'] = [None]*len(input)
        
        result = []
        for _, row in input.iterrows():
            lights = self.light_generation(row['heavy'], row['germline_seed'], row['custom_seed'], row['cdrs'], numbering_scheme, n, row['filtering'])
            result.append(pd.DataFrame({'heavy': [row['heavy']]*n,
                                        'generated_light': lights}))
        return pd.concat(result)

    def get_possible_seeds(self, germline):
        """Use lookup tables to find all possible seeds
        for the requested germline.
        """
        if len(germline) == 1:
            # Type seed
            if not germline in ['K', 'L']:
                raise SyntaxError(f"Light sequence type {germline} doesn't exist.")
            return MAP_TYPE_SEED[germline]
        elif '-' in germline:
            # V-gene seed
            try:
                return MAP_GENE_SEED[germline]
            except:
                raise SyntaxError(f"Light sequence V-gene {germline} doesn't exist.")
        else:
            # V-family seed
            try:
                return MAP_GENE_FAM_SEED[germline]
            except:
                raise SyntaxError(f"Light sequence V-gene family {germline} doesn't exist.")
            

    def light_log_likelihood(self, input):
        """Extract model conditional log likelihood for pairing
        
        Parameters
        ----------
        input : DataFrame
            Dataframe containing a column with heavy sequences and with light sequences
        """
        if not 'heavy' in input.columns:
            raise SyntaxError("The input dataframe should contain a column named 'heavy' with the heavy sequence")
        if not 'light' in input.columns:
            raise SyntaxError("The input dataframe should contain a column named 'light' with the light sequence")
        
        log_likelihoods = []
        for _, row in input.iterrows():
            log_likelihoods.append(round(self.LICHEN.likelihood_light(row['heavy'], row['light']),2))
        input['log_likelihood'] = log_likelihoods
        
        return input
    

    def light_perplexity(self, input):
        """Extract model perplexity for pairing
        
        Parameters
        ----------
        input : DataFrame
            Dataframe containing a column with heavy sequences and with light sequences
        """
        if not 'heavy' in input.columns:
            raise SyntaxError("The input dataframe should contain a column named 'heavy' with the heavy sequence")
        if not 'light' in input.columns:
            raise SyntaxError("The input dataframe should contain a column named 'light' with the light sequence")
        
        perplexities = []
        for _, row in input.iterrows():
            log_likelihood = self.LICHEN.likelihood_light(row['heavy'], row['light'])
            avg_log_prob = log_likelihood / len(row['light'])
            perplexities.append(round(math.exp(-avg_log_prob),2))

        input['perplexity'] = perplexities
        
        return input







            
